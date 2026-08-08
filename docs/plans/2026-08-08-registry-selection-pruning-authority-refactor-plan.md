# Registry Selection And Pruning Authority Refactor Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.4.1]; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.5]; docs/specifications/05-Message_Flow_and_State.md [MF-3.1], [MF-7]; docs/specifications/07-System_Invariants.md [OBS.1], [OBS.12], [MANAGER.14]; docs/specifications/08-Testing_Strategy.md [TS-3], [TS-3.1]
Superseded by: none

Class: 4 — the work refactors two runtime readers that derive both a selected
registry view and authority to delete queue rows. The intended behavior is
unchanged, but an incorrect split could erase live endpoint or manager evidence,
change manager-election outcomes, or make returned state disagree with the rows
selected for pruning.

Plan type: implementation without a normative behavior change.

Hardening: required. Both target functions cross queue-backed runtime-state,
liveness-evidence, canonical-selection, and destructive-cleanup boundaries.

## 1. Goal

Remove the dedicated-plan `C901` suppressions from:

- `weft/core/endpoints.py::list_resolved_endpoints` (`RUFF-SUP-004`)
- `weft/core/manager_runtime.py::_snapshot_registry` (`RUFF-SUP-015`)

The refactors must make selection and pruning authority easier to inspect while
preserving one essential property: the returned view and every deletion
candidate are derived from the same normalized snapshot and the same liveness
evidence. Queue reads, deletes, and ownership remain in the current runtime
owners. No shared registry framework, new persistence model, or new liveness
policy is in scope.

Success means both functions pass Ruff's complexity-10 gate without a `C901`
directive, every existing observable contract remains unchanged, the two
suppression rows and directives are removed exactly, and clean Python-expert
reviews find each refactor net positive for logical locality and
comprehensibility.

## 2. Requested Outcomes

- [x] Preserve endpoint latest-row reduction per `(name, tid)`, active-only
  filtering, task-status and TID-mapping liveness proof, lowest-live-TID
  canonicalization, `live_candidates`, deterministic public result order, and
  best-effort stale-row deletion.
- [x] Preserve manager normalization, latest-included-row selection per TID,
  optional pruning, definitive-stale authority, namespace ambiguity, bounded
  matched-PONG rescue, probe-controlled destructive authority, queue ownership,
  and best-effort deletion.
- [x] Keep each registry's decision semantics local to its owner. Do not build a
  generic endpoint/manager registry reducer merely because both functions scan
  queues.
- [x] Add direct firing proof for every branch whose movement could change
  selection or deletion authority, including operational delete failures.
- [x] Remove `RUFF-SUP-004` and `RUFF-SUP-015` only after the corresponding
  source directive is unnecessary and the refactor has passed clean review.
- [x] Change no public API, CLI shape, TaskSpec schema, queue name, record
  format, liveness rule, canonical-owner rule, or destructive-pruning policy.

## 3. Source Documents And Historical Context

Normative owners:

- `docs/specifications/01-Core_Components.md` [CC-2.4.1] owns the endpoint
  registry as a thin discovery primitive and specifies conflict-tolerant
  lowest-TID canonicalization with observable duplicate live claimants.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.5] owns
  `weft.state.endpoints` as ordinary broker-backed, runtime-only state and
  forbids backend-specific SQL coupling.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3.1] owns endpoint
  stale-owner pruning from task-log and TID-mapping evidence and specifies that
  missing-name resolution does not create or redirect work.
- `docs/specifications/03-Manager_Architecture.md` [MA-1] owns manager registry
  reduction, stale pruning, canonical live-manager selection, namespace
  ambiguity, external-supervisor evidence, and bounded PONG rescue. [MA-3]
  keeps manager selection inside the shared lifecycle owner.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-7] owns the bootstrap
  consequences of manager selection and requires ambiguous evidence to remain
  conservative.
- `docs/specifications/07-System_Invariants.md` [OBS.1], [OBS.12], and
  [MANAGER.14] make queue evidence observable, make a matched keyed PONG
  positive liveness proof only, and require stale or ambiguous manager evidence
  to degrade confidence without halting convergence.
- `docs/specifications/08-Testing_Strategy.md` [TS-3] owns the complexity-10
  simplify-or-register policy; [TS-3.1] owns exact local directives, stable
  suppression identities, human registry rows, raw inventory, and checker
  reconciliation.
- `docs/ruff-suppression-registry.md` currently records why
  `RUFF-SUP-004` and `RUFF-SUP-015` were deferred until this lifecycle-aware
  plan existed.

Historical plans are rationale, not current behavior contracts:

- `docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`
- `docs/plans/2026-04-17-canonical-owner-fence-plan.md`
- `docs/plans/2026-05-07-runtime-state-pruning-plan.md`
- `docs/plans/2026-05-07-manager-selection-ping-pong-liveness-plan.md`
- `docs/plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md`

## 4. Context And Key Files

Required repository guidance for every task:

- `AGENTS.md`
- `docs/agent-context/README.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/lessons.md` and `docs/lessons.md`

Task 1, endpoint characterization:

- modify `tests/tasks/test_task_endpoints.py` for direct endpoint resolver
  selection/deletion characterization using real context queues
- modify `tests/commands/test_queue.py` for public resolve/list result shape,
  declared order, duplicate count, pattern isolation, and exact stale deletion
- modify `tests/commands/test_runtime_prune.py` only where its existing live
  duplicate fixtures are the clearest proof that resolution does not acquire
  full-history prune authority
- read first: `weft/core/endpoints.py`, the `iter_queue_json_entries` queue-entry
  iterator in `weft/helpers/__init__.py`, and the endpoint builders and fixtures
  already used by those three test files; endpoint TID-mapping reduction is
  local to `weft/core/endpoints.py::_latest_tid_mapping_entries`
- reuse `build_context`, real nonpersistent registry queues,
  `build_endpoint_record_payload`, and `iter_queue_json_entries`; do not invent
  a fake repository or replace the whole queue scanner

Task 1, manager characterization:

- modify `tests/commands/test_manager_commands.py` for direct
  `_snapshot_registry` decision-table and queue-ownership proof
- modify `tests/cli/test_cli_manager.py` only for an end-to-end public
  list/status consequence not visible at the direct owner seam
- read first: `weft/core/manager_runtime.py`,
  `weft/core/control_probe.py`, `weft/core/service_convergence.py`,
  `weft/ext.py::RunnerHandle`, `weft/runtime_liveness.py`, and the manager
  payload/runtime-handle fixtures in `tests/commands/test_manager_commands.py`
- reuse the real `weft.state.services` queue and existing bounded PONG/runtime
  proof seams; do not mock `_snapshot_registry` in its own tests

Task 2, endpoint refactor:

- modify `weft/core/endpoints.py`, the endpoint tests named above,
  `docs/ruff-suppression-registry.md`, and
  `tests/specs/test_ruff_policy.py`; the registry file includes the generated
  index rewritten by the repository checker
- read first: [CC-2.4.1], [SB-0.5], [MF-3.1], the complete current
  `list_resolved_endpoints`, `_record_owner_is_live`,
  `_latest_task_statuses`, and `_latest_tid_mapping_entries`
- shared path to reuse: `canonical_owner_tid`; do not duplicate its numeric TID
  selection rule

Task 3, manager refactor:

- modify `weft/core/manager_runtime.py`, the manager tests named above,
  `docs/ruff-suppression-registry.md`, and
  `tests/specs/test_ruff_policy.py`; the registry file includes the generated
  index rewritten by the repository checker
- read first: [MA-1.4], [MA-3], [MF-7], [OBS.12], [MANAGER.14], the complete
  current `_snapshot_registry`, `_manager_record_stale_status`,
  `_manager_record_has_matched_pong`, `is_canonical_manager_record`, and
  `_host_pid_visibility_is_namespace_ambiguous`
- shared path to reuse: `pong_proves_dispatch_eligible` through the existing
  manager-runtime adapter; do not add a second PONG validator

Task 4, policy and traceability reconciliation:

- modify `docs/ruff-suppression-registry.md`,
  `tests/specs/test_ruff_policy.py`, this plan, `docs/plans/README.md`, and the
  backlink/implementation-mapping paragraphs in
  `docs/specifications/01-Core_Components.md` and
  `docs/specifications/03-Manager_Architecture.md`, plus the Related Plans list
  in `docs/specifications/08-Testing_Strategy.md`
- read first: `docs/specifications/08-Testing_Strategy.md` [TS-3], [TS-3.1],
  `bin/ruff_suppression_index.py`, `tests/specs/test_ruff_policy.py`,
  `tests/specs/test_ruff_suppression_index.py`, and
  `tests/specs/test_plan_metadata.py`
- generated path to reuse: run the repository checker in write mode; do not
  hand-edit the generated suppression index

Task 5, final hardening:

- modify only files required to close findings from the named gates or clean
  review; any wider production change requires a recorded deviation and scope
  review
- read first: the complete diff, this plan's Evidence Log and Deviation Log,
  and every reviewer report

Current owners and contracts:

- `list_resolved_endpoints` owns endpoint queue acquisition, history reduction,
  liveness evidence acquisition, canonical result construction, exact stale-ID
  deletion, and closure today
- `_snapshot_registry` owns manager queue acquisition or caller-queue use,
  normalization, stale/PONG/ambiguity disposition, latest included-row
  selection, exact timestamp deletion, and conditional closure today
- `bin/ruff_suppression_index.py` owns suppression-registry reconciliation and
  generated-index writes; it is a Python file without executable mode and must
  be invoked through the repo-managed interpreter

## 5. Spec Baseline

Repository baseline at plan authoring:

- commit: `75cc1f688b3fc2e1ce93572d95fd17b2d48d3a2c`
- Ruff: `0.16.2`
- landing uses explicit file-list staging against this plan's owned delta;
  preserve and exclude every unrelated change

Raw complexity evidence:

```text
weft/core/endpoints.py:332:5: C901 `list_resolved_endpoints` is too complex (13 > 10)
weft/core/manager_runtime.py:176:5: C901 `_snapshot_registry` is too complex (13 > 10)
```

Reproduce with:

```bash
./.venv/bin/ruff check --ignore-noqa --select C901 --output-format concise \
  weft/core/endpoints.py weft/core/manager_runtime.py
```

The command is expected to exit 1 at baseline and also reports other manager
runtime findings outside this plan. Acceptance is scoped by symbol, not by
requiring this two-file raw command to become globally clean.

Current endpoint flow:

```text
weft.state.endpoints rows
        |
        v
latest valid row per (name, tid) + task/TID evidence
        |
        +--> live rows grouped by name --> lowest live TID --> returned view
        |
        +--> stale latest-row IDs -------------------------> best-effort delete
```

Current manager flow:

```text
weft.state.services rows
        |
        v
normalize record + stale/PONG/namespace/probe decision
        |
        +--> included rows --> latest included row per TID --> returned snapshot
        |
        +--> authorized stale timestamps -----------------> best-effort delete
```

The shared branch point is intentional in both flows. The plan may give that
decision a name, but it must not put returned-view construction and deletion
authorization on independent scans or independent evidence paths.

## 6. Spec Baseline And Traceability Strategy

The current specs already describe the intended behavior. No normative delta is
proposed. This is a Class 4 implementation refactor, not a Class 5 contract
change.

Proposed Spec Delta: **none**. There is no spec-promotion slice. Each refactor,
its firing tests, its local-directive removal, its human registry-row removal,
the generated index, and the matching policy-count change land as one atomic
suppression-close slice. Traceability-only reciprocal plan backlinks land no
later than the second close slice. The plan remains `draft` until both slices
and final hardening are complete, then changes to `completed`.

If implementation requires changing which rows are returned, retained,
omitted, or deleted; changes what constitutes live, stale, definitive, or
ambiguous evidence; changes PONG authority; or changes public ordering, stop.
Revise this work as Class 5 with an exact Proposed Spec Delta and owner review
before making the behavior change.

Final traceability edits:

- add this plan to the implementation-plan backlinks adjacent to
  [CC-2.4.1]
- add this plan to the [MA-1.4] implementation mapping or adjacent manager
  lifecycle plan backlinks
- add this plan to the Related Plans list governing [TS-3] and [TS-3.1]
- update implementation mappings only if private symbol ownership changes;
  do not rewrite normative behavior text for a no-behavior-change refactor

## 7. Protected Invariants

### 7.1 Endpoint registry

1. **One scan, one evidence frame.** Returned endpoints and stale deletion IDs
   come from the same latest-by-owner rows and the same task-status/TID-mapping
   evidence.
2. **Latest row means latest row.** Only the latest valid matching row per
   `(name, tid)` participates in current liveness classification. This effort
   must not turn opportunistic resolution into complete historical compaction.
3. **Active-only view.** Non-active endpoint rows are not returned and are not
   newly classified for deletion by this refactor.
4. **Conservative liveness.** `_record_owner_is_live` remains the authority;
   the refactor must not duplicate or approximate its host/runtime proof.
5. **Canonical conflict behavior.** Every live duplicate remains observable in
   `live_candidates`; the lowest numeric eligible TID is canonical.
6. **Ordering.** Public results remain sorted by `(name, numeric tid)`. This is
   an explicit output contract, not accidental mapping iteration.
7. **Best-effort delete.** `BrokerError`, `OSError`, and `RuntimeError` from a
   stale-row delete do not replace the selected view or prevent later deletion
   attempts. Other defects remain visible.
8. **Queue ownership.** `list_resolved_endpoints` opens and closes exactly its
   own endpoint registry queue.

### 7.2 Manager registry

1. **One scan, one evidence frame.** The snapshot inclusion decision and any
   delete authorization are made from the same normalized record and probe
   evidence.
2. **Latest included row per TID.** A newer included timestamp replaces an
   older included timestamp. Mapping iteration order is not selection
   authority.
3. **Pruning disabled means no stale filtering or deletion.** When
   `prune_stale=False`, valid normalized rows remain eligible for snapshot
   selection without liveness probing.
4. **Decision ladder remains exact:**
   - keep non-active or non-stale rows
   - keep a non-definitive canonical row rescued by a matched PONG
   - prune and omit definitive-stale rows
   - prune and omit non-definitive, non-ambiguous stale rows only when
     `probe_stale=True`
   - omit without deleting non-definitive, non-ambiguous stale rows when the
     caller did not authorize destructive probing
   - keep non-definitive namespace-ambiguous rows when no stronger authority
     exists
5. **PONG is positive evidence only.** Absence, timeout, malformed response, or
   mismatched response does not become definitive death proof.
6. **Ambiguity is not deletion authority.** Namespace ambiguity cannot be
   simplified into stale and cannot authorize destructive cleanup by itself.
7. **Queue ownership.** `_snapshot_registry` closes only a queue it opened; a
   caller-supplied queue remains caller-owned.
8. **Best-effort delete.** The existing operational failure families remain
   contained per timestamp; unexpected defects remain visible.

### 7.3 Cross-cutting

- No backend SQL, second registry store, cache, or persistent projection.
- No generic registry repository or shared endpoint/manager reducer.
- No helper receives a live `Queue` unless it is a narrow, already-owned I/O
  helper with no selection policy. The preferred design keeps all queue I/O in
  the two current top-level owners.
- No public result type or serialization change.
- No correctness assertion depends on dict or set iteration order. Where order
  is observable, tests declare the ordering rule directly.

## 8. Proposed Internal Design

Names below are proposed, not mandatory. Review the resulting ownership, not
the spelling.

### 8.1 Endpoint reduction

Return the two coupled products from one private reducer. Start with the
simplest typed representation that keeps their meanings obvious, such as a
two-field tuple. Introduce a private immutable result type only if named fields
materially improve the implementation review. A possible shape is:

```python
@dataclass(frozen=True, slots=True)
class _EndpointResolutionReduction:
    resolved: tuple[ResolvedEndpoint, ...]
    stale_message_ids: tuple[int, ...]
```

A private reducer receives the already selected latest endpoint records plus
the exact task-status and TID-mapping evidence. It performs active filtering,
liveness classification, grouping, canonical selection, duplicate counting,
and deterministic result ordering. It returns the selected view and deletion
candidates together.

`list_resolved_endpoints` retains:

- endpoint queue acquisition and closure
- queue iteration, JSON/record normalization, pattern filtering, and
  latest-by-owner reduction
- task-status and TID-mapping acquisition
- best-effort application of exactly the returned deletion IDs
- conversion of the private tuple to the existing public list

A second tiny helper for latest-by-owner selection is acceptable only if it
does not hide record recency or require a state-carrier object. Extraction is
not a goal by itself.

### 8.2 Manager disposition

Introduce one explicit private disposition value, such as
`Literal["keep", "omit", "prune"]` or a small enum. A private helper receives
one normalized record plus `context`, `probe_stale`, and `probe_cache`, then
owns the existing stale/PONG/namespace decision ladder.

`_snapshot_registry` remains responsible for bypassing this helper when
`prune_stale=False` or the normalized row is not active. For an active row under
pruning, the helper returns `keep` when the existing stale-status authority says
the row is live/non-stale, and otherwise applies the exact table in Task 1.

`_snapshot_registry` retains:

- queue acquisition and caller-owned versus locally-owned closure
- queue scan and manager-record normalization
- latest-included-row selection per TID
- collection and best-effort application of timestamps whose disposition is
  `prune`
- the existing dictionary return shape

The helper must call the existing `_manager_record_stale_status`,
`is_canonical_manager_record`, `_manager_record_has_matched_pong`, and
`_host_pid_visibility_is_namespace_ambiguous` authorities. It must not copy
their logic. An enum is preferable if it makes destructive authority explicit;
a boolean pair is acceptable only if every combination has one unambiguous
meaning.

### 8.3 Why the two designs stay separate

Endpoint resolution selects one canonical live owner per endpoint name and
reports duplicate live claimants. Manager snapshotting selects the latest
included record per manager TID and feeds later leadership reduction. Their
liveness evidence, grouping keys, ambiguity policy, and deletion authority are
not interchangeable. A shared framework would reduce line count by replacing
domain names with callbacks and flags, which would make destructive authority
harder to audit.

## 9. Implementation Tasks

### Task 1 — Lock characterization and authority tables

Owner: endpoint and manager runtime owner tests.

Boundary: existing behavior only. Because this is a no-behavior-change
refactor, characterization-first is the red-green equivalent: the new tests
must pass on the pre-refactor implementation and fail under deliberate local
mutations of each protected branch.

Endpoint proof to add or strengthen:

- latest valid row per `(name, tid)` controls liveness and deletion selection
- two live claimants return the lowest numeric TID with
  `live_candidates == 2`, independent of insertion order
- a stale latest row is omitted and its exact message ID is deleted
- a live duplicate is not deleted while another claimant is stale
- one operational delete failure does not change the returned view and does
  not prevent a later authorized deletion attempt
- an unexpected non-operational delete defect propagates
- pattern filtering does not classify or delete an out-of-pattern row

Manager proof to add or strengthen as an explicit table:

| Row/evidence | `prune_stale` | `probe_stale` | PONG seam called? | Expected view | Delete? |
| --- | --- | --- | --- | --- | --- |
| non-active or live | either | either | no | keep latest | no |
| definitive stale | true | either | no | omit | yes |
| non-definitive stale, canonical, matched PONG | true | either | yes | keep | no |
| non-definitive stale, canonical, no matched PONG, non-ambiguous | true | false | yes | omit | no |
| non-definitive stale, canonical, no matched PONG, non-ambiguous | true | true | yes | omit | yes |
| non-definitive stale, noncanonical, non-ambiguous | true | false | no | omit | no |
| non-definitive stale, noncanonical, non-ambiguous | true | true | no | omit | yes |
| non-definitive stale, canonical, no matched PONG, namespace-ambiguous | true | either | yes | keep | no |
| non-definitive stale, noncanonical, namespace-ambiguous | true | either | no | keep | no |
| any valid row | false | either | no | keep latest | no |

Also prove:

- later included timestamp wins for the same TID regardless of input order
- a newer omitted/pruned row does not silently rewrite the meaning of the
  existing included-row rule; pin the current result explicitly
- caller-supplied queue is not closed; locally acquired queue is closed
- one operational delete failure does not block later authorized deletes or
  change the returned snapshot
- an unexpected delete defect propagates
- absent/malformed/mismatched PONG is not positive proof and not independent
  deletion authority
- a noncanonical manager row does not gain PONG-rescue authority merely because
  `probe_stale=True`
- the PONG seam is called only for the same canonical, non-definitive stale
  records that the current implementation probes; the table tests assert both
  positive calls and skipped calls

Prefer real broker queues for scan, timestamp, deletion, and closure behavior.
Monkeypatch only bounded process/runtime/PONG proof seams where real process
evidence would make the test nondeterministic. Do not replace the whole queue or
the whole target function with mocks.

Verification:

```bash
./.venv/bin/python -m pytest -q -n 0 \
  tests/tasks/test_task_endpoints.py \
  tests/commands/test_queue.py \
  tests/commands/test_runtime_prune.py \
  tests/commands/test_manager_commands.py \
  tests/cli/test_cli_queue.py \
  tests/cli/test_cli_manager.py
```

Required action: record the exact added test node IDs and the mutation each one
detects in this plan's Evidence Log before refactoring.

### Task 2 — Refactor endpoint selection with coupled deletion output

Owner: `weft/core/endpoints.py::list_resolved_endpoints` and its new private
reducer/result.

Steps:

1. Add the smallest cohesive reducer justified by Task 1's table. Use the
   simplest coupled typed return; add a private immutable result type only if
   its named fields improve the clean locality review.
2. Keep queue scan, evidence acquisition, delete application, and closure in
   `list_resolved_endpoints`.
3. Run endpoint owner tests and raw Ruff with `--ignore-noqa`. Confirm
   `list_resolved_endpoints` is at or below complexity 10 while leaving the
   approved directive and registry row in place during review.
4. Dispatch a clean subagent acting as a Python expert to evaluate whether the
   refactor is net positive or net negative, focused on logical locality and
   comprehensibility. The reviewer must compare against the pre-refactor code
   and inspect the destructive-authority proof.
5. If the verdict is net negative, keep the implementation in the rework queue.
   Do not revert it automatically. Keep both the source directive and registry
   row active, then rework until a different clean reviewer returns net
   positive.
6. Only after a net-positive verdict, close the endpoint suppression as one
   non-separable policy slice. There must be no commit between these actions:
   - remove the `RUFF-SUP-004` source directive and human registry row
   - exclude `4` alongside `21` in the first `EXPECTED_GROUP_IDS` range
   - recompute the baseline counts (`234 -> 233`, `377 -> 376`,
     `C901 143 -> 142`) and the global raw inventory
   - run `./.venv/bin/python bin/ruff_suppression_index.py --write`
   - pass focused endpoint tests, policy tests, normal Ruff plus `RUF100`, and
     the suppression checker
7. The Task 2 source/test candidate remains uncommitted while clean review is
   pending. Task 2 becomes a committable completed slice only after step 6 is
   green. If the repository baseline has moved, derive the exact counts and ID
   exclusion from current evidence rather than copying the numbers above.

Stop conditions:

- the reducer performs queue I/O
- liveness logic is duplicated instead of delegated
- returned rows and delete IDs can be computed in separate calls or evidence
  frames
- latest-by-owner or public result ordering becomes implicit mapping order
- the source becomes shorter but requires readers to chase more state than the
  original function

### Task 3 — Refactor manager selection with explicit disposition

Owner: `weft/core/manager_runtime.py::_snapshot_registry` and its new private
disposition helper/value.

Steps:

1. Encode the Task 1 manager decision table as one explicit local disposition
   helper.
2. Keep normalization, scan, latest included-row selection, delete application,
   and queue ownership in `_snapshot_registry`.
3. Run manager owner tests and raw Ruff with `--ignore-noqa`. Confirm
   `_snapshot_registry` is at or below complexity 10 while leaving the approved
   directive and registry row in place during review. Other raw C901 findings
   in `manager_runtime.py` are out of scope and remain registered.
4. Dispatch a new clean Python-expert subagent with no prior implementation
   context. The review must judge net positive or net negative specifically on
   logical locality, comprehensibility, and visibility of destructive
   authority.
5. Queue a net-negative result for rework rather than reverting it. Keep both
   the source directive and registry row active until a reworked candidate
   earns a net-positive clean review.
6. Only after a net-positive verdict, close the manager suppression as one
   non-separable policy slice. There must be no commit between these actions:
   - remove the `RUFF-SUP-015` source directive and human registry row
   - exclude `15` alongside the already removed `4` and existing `21` in the
     first `EXPECTED_GROUP_IDS` range
   - recompute the expected final counts (`233 -> 232`, `376 -> 375`,
     `C901 142 -> 141`) and the global raw inventory
   - run `./.venv/bin/python bin/ruff_suppression_index.py --write`
   - pass focused manager tests, policy tests, normal Ruff plus `RUF100`, and
     the suppression checker
7. The Task 3 source/test candidate remains uncommitted while clean review is
   pending. Task 3 becomes a committable completed slice only after step 6 is
   green. If Task 2 or the repository baseline has changed, derive the exact
   counts and ID exclusion from current evidence.

Stop conditions:

- the disposition helper opens, closes, scans, or deletes from a queue
- PONG absence becomes negative proof
- namespace ambiguity authorizes deletion
- `probe_stale=False` gains destructive authority
- latest-row choice starts depending on dict iteration
- a generic registry abstraction is introduced
- fixing the complexity score requires changing the manager contract

### Task 4 — Reconcile final inventory and traceability

Owner: suppression registry, generated index, policy tests, and governing spec
backlinks.

After both atomic suppression-close slices have net-positive clean reviews and
green policy gates:

1. Verify both directives, human rows, and generated-index entries are absent;
   do not defer either row removal from Tasks 2 or 3 into this task.
2. Verify the exact reviewed inventory and policy constants from the current
   repository state. At this plan's baseline the cumulative deltas are:
   - suppression groups: `234 -> 232`
   - source directives: `377 -> 375`
   - raw `C901`: `143 -> 141`
   These numbers are evidence, not blind edit instructions. Recompute them if
   the baseline moves.
3. Verify the first `EXPECTED_GROUP_IDS` generator excludes `4`, `15`, and the
   pre-existing gap `21`; do not merely change the three count constants.
4. Run the checker in `--check` mode and prove no orphan directive, missing
   group, cardinality mismatch, or raw inventory drift remains.
5. Add the reciprocal plan backlinks described in Section 6.
6. Update this plan's status to `completed`, check the completed outcomes, and
   record final commands, reviews, and any deviations.

Do not renumber any remaining `RUFF-SUP` group. Stable IDs are audit identities,
not a dense sequence.

### Task 5 — Final hardening and completed-work review

Run the focused suites first, then repository gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest -q -n 0 \
  tests/tasks/test_task_endpoints.py \
  tests/commands/test_queue.py \
  tests/commands/test_runtime_prune.py \
  tests/commands/test_manager_commands.py \
  tests/cli/test_cli_queue.py \
  tests/cli/test_cli_manager.py
./.venv/bin/python -m pytest -q -n 0 \
  tests/specs/test_ruff_policy.py \
  tests/specs/test_ruff_suppression_index.py \
  tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py
./.venv/bin/python bin/ruff_suppression_index.py --check
```

Then run the complete repository definition-of-done gates. Both pytest commands
are required: the first is the ordinary suite, and the second includes tests
marked outside the default selection.

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff check --extend-select RUF100 .
./.venv/bin/ruff format --check weft tests integrations/weft_django \
  extensions/weft_docker extensions/weft_macos_sandbox \
  extensions/weft_microsandbox
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n 0 \
  tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
bin/check-dom15-fixtures
bin/check-doc-paths
../backstitch/.venv/bin/backstitch check \
  --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json
uv lock --check
git diff --check
```

`bin/check-doc-paths` and Backstitch have reviewed repository-wide baseline
debt. Compare findings by code, path, symbol, and spec section. This effort must
add no finding keyed to this plan, [CC-2.4.1], [MA-1.4], either changed runtime
symbol, or the suppression-policy surfaces. Aggregate-count equality is not a
substitute for keyed comparison. If `../backstitch` is unavailable, record the
tooling blocker; do not report the traceability gate as passed.

If an unrelated change makes a gate fail, report the external worktree conflict
without rewriting or reverting out-of-scope files. Completion still requires a
clean result against the integrated repository state.

Raw symbol acceptance:

```bash
./.venv/bin/ruff check --ignore-noqa --select C901 --output-format json \
  weft/core/endpoints.py weft/core/manager_runtime.py
```

Parse the JSON and prove there is no C901 diagnostic for
`list_resolved_endpoints` or `_snapshot_registry`; do not require unrelated
manager-runtime findings to disappear.

Then dispatch a clean completed-work reviewer. The reviewer must read this plan
and governing specs, inspect the actual diff, run the exact firing tests, and
report findings by severity. Completion requires no unresolved blocker or
high-severity finding and explicit confirmation that deletion authority did not
move away from the selected evidence frame.

## 10. Test Strategy And Adversarial Probes

The test strategy is decision-table plus real queue effects. Pure helper tests
may supplement but cannot replace owner-level proof that selected output and
deleted rows agree.

Required adversarial mutations include:

- reverse endpoint row insertion order and live-candidate insertion order
- reverse manager row insertion order for the same TID
- make the newest endpoint owner row stale while an older row is live-looking
- make one delete fail and verify later deletes are still attempted
- make a delete raise an unexpected defect and verify it is not hidden
- flip manager `probe_stale` while holding all evidence constant
- change definitive stale to non-definitive stale
- add and remove namespace ambiguity while holding PID evidence constant
- supply matched, absent, malformed, and mismatched PONG evidence
- pass a caller-owned manager registry queue and prove it remains usable after
  `_snapshot_registry`

Every test must assert observable rows, returned records, close ownership, or
exact probe calls. Tests that merely assert a private helper's enum value are
insufficient for destructive behavior.

## 11. Independent Review Loop

This effort has three mandatory clean-review points:

1. **Plan review:** before implementation, a clean reviewer checks source-spec
   alignment, invariant completeness, test fidelity, rollback, and whether the
   proposed seams preserve locality.
2. **Per-refactor Python review:** after each of Tasks 2 and 3, a different
   clean Python expert returns `NET POSITIVE` or `NET NEGATIVE`, focusing on
   logical locality and comprehensibility. A negative candidate enters the
   rework queue and is not silently reverted.
3. **Completed-work review:** after policy reconciliation, a clean reviewer
   audits the whole diff and exact firing evidence.

Reviewer bootstrap must include:

- this plan
- the exact governing spec sections
- both target functions as they existed at the baseline commit
- the changed source and tests
- `RUFF-SUP-004` and `RUFF-SUP-015` registry rows
- the raw symbol-scoped C901 evidence

The reviewer must not treat lower complexity as sufficient. A refactor is net
negative if policy becomes more remote, state has to be threaded through
generic helpers, or readers can no longer see why a row is safe to delete.

## 12. Rollout, Rollback, And One-Way Doors

Rollout is an atomic internal release. There is no schema, data, CLI, or config
migration. Existing runtime queues remain readable across mixed process
versions because record formats and public functions do not change.

Rollback is one code rollback covering source, tests, directives, registry,
generated index, policy counts, plan status, and spec backlinks. Runtime queue
data requires no migration.

The destructive one-way door is not deployment; it is each runtime delete.
Therefore rollback cannot recover a row deleted under broadened authority. The
implementation must prevent that case before landing:

- deletion candidates are returned from the same decision that creates the
  selected view
- characterization tests pin exact message IDs/timestamps before refactoring
- ambiguity and missing PONG never become new deletion authority
- best-effort failure does not trigger broader retry or full-history cleanup

If a refactor cannot meet those conditions while reducing C901, retain the
approved suppression. Removing a lint directive is not worth weakening runtime
evidence safety.

## 13. Out Of Scope

- changing endpoint registration, unregister, heartbeat, lease, or replacement
  behavior
- making endpoint resolution compact all historical rows
- changing runtime-prune command policy (`RUFF-SUP-033` or `RUFF-SUP-059`)
- changing manager election, startup grace, dispatch authority, stop behavior,
  or manager diagnostic projection
- refactoring any other C901 finding in `manager_runtime.py`
- a generic runtime-registry abstraction shared by endpoints, managers,
  services, TID mappings, or streaming state
- backend-specific SimpleBroker access
- new metrics, logs, warnings, or public diagnostic fields
- renumbering suppression IDs

## 14. Evidence Log

Record ordinary characterization and verification evidence here, not in the
Deviation Log.

| Date/task | Exact command or test node | Mutation/branch proved | Result/reviewer |
| --- | --- | --- | --- |
| 2026-08-08 / clean Codex plan review | `python -m pytest -q -n 0 tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py`; `python bin/ruff_suppression_index.py --check`; `git diff --check` | Metadata/index integrity plus same-family independent audit of canonical-only PONG rescue, destructive authority, locality, rollback, and exact task seams | 8 tests passed; checker and diff check passed; clean Codex review `NET POSITIVE` |
| 2026-08-08 / outside-model plan review | Claude Opus read-only review of this plan, governing specs, target code, registry rows, tests, and runbooks | Exact endpoint/manager decision fidelity plus review evidence and atomic suppression-policy sequencing | Core runtime design judged faithful; `BLOCKED` on pre-checked outside-review evidence and a red policy-gate window across Tasks 2–4; plan revised for re-review |
| 2026-08-08 / outside-model plan re-review | Claude Opus 4.8 read-only re-review in the same session after corrections | Rechecked both prior blockers, intermediate policy arithmetic, exact ID exclusions, helper ownership, result-type locality, and per-task file inventory | `NET POSITIVE`; no P0/P1/P2 findings; optional file-inventory nit corrected before handoff |
| 2026-08-08 / Task 1 endpoint characterization | `tests/tasks/test_task_endpoints.py::{test_endpoint_resolution_uses_latest_owner_row_for_view_and_stale_delete,test_endpoint_resolution_is_order_independent_and_preserves_live_claimants,test_endpoint_resolution_continues_after_operational_delete_failure,test_endpoint_resolution_propagates_unexpected_delete_defect,test_endpoint_resolution_pattern_does_not_delete_nonmatching_stale_row,test_endpoint_resolution_closes_acquired_registry_queue}`; `tests/commands/test_queue.py::test_list_command_endpoints_uses_lowest_live_tid_as_canonical` | Latest-owner frame, declared numeric/order behavior, exact stale/live rows, operational continuation, unexpected propagation, pattern isolation, and success/defect closure | Combined endpoint/manager owner command passed; endpoint delegated owner set 43 passed; Ruff/format/diff passed |
| 2026-08-08 / Task 1 manager characterization | `tests/commands/test_manager_commands.py::{test_snapshot_registry_decision_table_uses_one_record_evidence_frame,test_snapshot_registry_latest_included_timestamp_wins,test_snapshot_registry_newer_filtered_row_preserves_older_included_row,test_snapshot_registry_does_not_close_caller_owned_queue,test_snapshot_registry_closes_locally_acquired_queue,test_snapshot_registry_operational_delete_failure_continues_later_deletes,test_snapshot_registry_propagates_unexpected_delete_defect,test_snapshot_registry_accepts_only_dispatch_eligible_matched_pong}` | Full keep/omit/prune table, exact PONG call/skip, latest included row, omitted/pruned-newer behavior, queue ownership, all operational delete families, unexpected propagation, and real PONG validation | 29 focused cases and 80-file owner tests passed; combined owner command plus Ruff/format/diff passed |
| 2026-08-08 / Task 2 first clean endpoint review | Clean Python review of the first `_reduce_endpoint_records` candidate against HEAD and the owner tests | Compared phase ordering, destructive authority, latest-row preconditions, locality, and raw C901 removal | `NET NEGATIVE`: result construction moved ahead of stale deletion, changing side effects when later selection failed; `RUFF-SUP-004` retained and candidate queued for rework |
| 2026-08-08 / Task 2 endpoint rework | `tests/tasks/test_task_endpoints.py::test_endpoint_resolution_deletes_stale_rows_before_selection_defect`; full endpoint owner set; raw symbol C901; Ruff, format, mypy, and diff check | The new test failed against the first candidate because the stale row remained; `_classify_latest_endpoint_records` now returns live groups plus exact stale IDs from one latest-row frame, while the owner applies deletion before canonical selection as HEAD did | Red proof reproduced; reworked test passed; 44 endpoint owner cases passed; raw C901 absent; focused quality gates passed |
| 2026-08-08 / Task 2 clean endpoint re-review | Different clean Python reviewer compared the rework against HEAD, the new failure-order proof, and endpoint owner tests | Rechecked phase order, destructive coupling, helper precondition, mapping-order independence, queue ownership, exception behavior, locality, and comprehensibility | `NET POSITIVE`; no blocker; approved atomic removal of `RUFF-SUP-004` |
| 2026-08-08 / Task 2 endpoint suppression close | Endpoint owner tests plus `tests/specs/test_ruff_policy.py`, `tests/specs/test_ruff_suppression_index.py`; checker; normal Ruff with `RUF100`; format; mypy; diff check | Removed source directive and human/generated group 004 records together; excluded ID 004; reconciled `234 -> 233` groups, `377 -> 376` directives, and `C901 143 -> 142` | 128 focused tests passed; checker and all focused quality gates passed |
| 2026-08-08 / Task 3 clean manager review | Clean Python reviewer compared `_manager_registry_disposition` and `_retain_latest_included_manager_record` against HEAD and the full decision table | Rechecked canonical-only PONG rescue, ambiguity, delete authority, probe behavior, queue ownership, exception behavior, locality, and newest-timestamp resolution | Initial proof gap found and fixed by reversing the actual real-row stream; adversarial last-iterated-wins mutation then failed; final verdict `NET POSITIVE` with no blocker |
| 2026-08-08 / Task 3 manager suppression close | Manager owner tests plus `tests/specs/test_ruff_policy.py`, `tests/specs/test_ruff_suppression_index.py`; checker; normal Ruff with `RUF100`; format; mypy; diff check | Removed source directive and human/generated group 015 records together; excluded ID 015; reconciled `233 -> 232` groups, `376 -> 375` directives, and `C901 142 -> 141` | 164 focused tests passed; checker and all focused quality gates passed |
| 2026-08-08 / final repository gates | Exact Task 5 focused commands; both full pytest selectors; `bin/pytest-pg --all`; full mypy/Ruff/RUF100/format; checker; DOM-15; doc paths; Backstitch; lock; diff | Integrated validation and traceability | Optional-extra floors were aligned at `simplebroker-pg>=3.5.1`. Default pytest passed 3,633 tests with 3 skips; explicit-marker pytest passed 3,634 tests with 14 skips. The PostgreSQL/xDist gate twice reached only the unrelated wall-clock assertion in `test_manager_child_termination_uses_one_deadline_for_multiple_children`; that exact node passed through `bin/pytest-pg --all` in isolation, and the complementary PostgreSQL run passed 3,572 tests with 12 skips. Focused suites, 198-file mypy, both Ruff passes, 412-file format, checker, DOM-15, lock, metadata/spec hygiene, and diff check passed. `check-doc-paths` retained eight reviewed baseline findings; Backstitch retained 45 errors, 1,025 warnings, and 610 infos with no issue matching this plan, either removed suppression, or any new helper. |
| 2026-08-08 / completed-work clean review | Independent Python review of the complete effort diff against HEAD, excluding unrelated owner changes | Semantic fidelity, locality/comprehensibility, firing proof, order rules, suppression arithmetic, and traceability | `NET POSITIVE`; no blocker or high-severity finding |

## 15. Deviation Log

Record implementation-time departures here. Each entry must name the task,
reason, affected invariant, proof added, and reviewer disposition.

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| [CC-2.4.1] / Task 2 | Preserve endpoint deletion and selection semantics while reducing local complexity | The first candidate constructed the canonical result before applying stale deletions, unlike the pre-refactor owner | Clean review found that an unexpected selection defect could suppress a previously authorized stale deletion; the candidate stayed uncommitted with its suppression active and was reworked to preserve the original phase order | None; implementation-only correction restored the specified behavior |
| Task 5 repository gate | Run the PostgreSQL suite once under its ordinary xDist configuration | The ordinary run twice failed only the existing multi-child termination wall-clock bound under load; the exact node passed through the same PostgreSQL harness in isolation, and all remaining PostgreSQL tests passed in one complementary run | This preserves a targeted close without weakening or rewriting an unrelated timing assertion; together the two runs execute the complete PostgreSQL selection | None; test timing policy is outside this effort |

## 16. Required-Reading Comprehension Questions

An implementer or reviewer must be able to answer these before editing:

1. Why must endpoint returned rows and stale message IDs be produced from one
   latest-by-owner/evidence frame?
2. Why is deleting every historical endpoint row not an incidental cleanup
   improvement?
3. Which endpoint ordering is declared, and which mapping orders must remain
   irrelevant?
4. What are the three manager dispositions, and which exact evidence can
   authorize `prune`?
5. Why can a matched PONG keep a manager row while a missing PONG cannot delete
   it?
6. When must a namespace-ambiguous manager row remain in the snapshot?
7. Who owns closing a caller-supplied manager registry queue?
8. Why are endpoint and manager reducers intentionally not shared?
9. What evidence would force this plan to stop and become a Class 5 spec
   change?
10. What must a clean Python-expert review establish beyond a lower C901 score?

## 17. Completion Checklist

- [x] Outside-model plan review is net positive with no blocker/high finding.
- [x] Characterization matrix passes against pre-refactor behavior.
- [x] Endpoint refactor passes owner tests and raw symbol-scoped C901 proof.
- [x] Endpoint clean Python review is net positive.
- [x] Manager refactor passes owner tests and raw symbol-scoped C901 proof.
- [x] Manager clean Python review is net positive.
- [x] `RUFF-SUP-004` and `RUFF-SUP-015` directives and registry rows are gone.
- [x] Registry inventory, generated index, and policy counts reconcile exactly.
- [x] Spec backlinks and implementation mappings are synchronized.
- [x] Focused tests, policy tests, Ruff, format, mypy, checker, and diff check
  pass.
- [x] Completed-work independent review has no unresolved blocker/high finding.
- [x] Plan status and plan-corpus index are updated atomically with completion.

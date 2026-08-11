# Task Snapshot Reducer Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/09-Implementation_Plan.md [IP-1], [IP-1.0]
Superseded by: none

Class: 4 — behavior-preserving refactor of lifecycle reconstruction. The work
changes the internal seam that implements public status policy on the durable
task-evidence path, so characterization, mutation proof, and independent review
are mandatory even though no public contract is intended to change.

## 1. Goal

Turn the 353-line `_collect_task_snapshot_records` procedure into a short I/O
orchestrator over one cohesive, pure MF-5 reducer. The reducer will fold task
events and reconcile already-collected runtime and queue evidence without
opening queues, reading clocks, selecting a manager, or loading runner plugins.
This lowers cyclomatic complexity while keeping lifecycle priority, stale
liveness, result recovery, ordering, and every public payload unchanged.

This is the third of four independent plans extracted from
[`2026-07-29-structural-review-remediation-plan.md`](./2026-07-29-structural-review-remediation-plan.md).
It has no file overlap with the validation-capability plan. If the
import-boundary plan lands first, use its direct sibling import style for the
new reducer rather than importing through `weft.commands`.

## 2. Source Documents

Governing specifications:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], especially the
  owner/boundary rule and the evidence-priority, terminal-precedence,
  stale-liveness, claimed-result, and active-manager clauses.
- `docs/specifications/09-Implementation_Plan.md` [IP-1] and [IP-1.0], which
  assign public reconstruction to commands over shared core evidence and keep
  presentation policy out of core/runtime and runner plugins.

Required guidance:

- `CLAUDE.md` §1.1: size is not a smell; extraction is justified here by the
  separable pure reduction boundary, not line count.
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md`

The umbrella structural plan is exploratory context only. This plan replaces
its Task 5 instructions; the specifications above remain normative.

## 3. Context and Key Files

### Current responsibility mix

`weft/commands/system.py::_collect_task_snapshot_records` currently performs
four different stages in one function:

```text
QUEUE / CLOCK I/O
  read now, service registry, manager selection, TID mappings, task log
            |
            v
EVENT FOLD
  apply task_activity and TaskSpec-bearing lifecycle events per TID
            |
            v
EVIDENCE ACQUISITION
  describe runtime; inspect ctrl_out/outbox; detect claimed residue
            |
            v
PURE POLICY + PRESENTATION
  choose public status/reconciliation/timestamps; build, filter, sort snapshots
```

The first and third stages belong in the command orchestrator. The second and
fourth are deterministic policy and belong together in one reducer module.
Do not split each policy helper into a separate file. They share the same
record, terminal precedence, and reconciliation vocabulary.

### Files to modify

- new `weft/commands/_task_snapshot_reducer.py`
- `weft/commands/system.py`
- new `tests/commands/test_task_snapshot_reducer.py`
- `tests/commands/test_status.py`
- `docs/specifications/05-Message_Flow_and_State.md`
- module docstrings or implementation mappings affected by ownership
- `docs/plans/README.md`

### Read first

- `weft/commands/system.py` from `_merge_runtime_entry` through
  `_collect_task_snapshot_records`, plus `_ServiceEvidence` and
  `_InternalServiceOwnerEvidenceIndex`
- `weft/core/task_evidence.py::TaskEvidenceSnapshot`,
  `task_local_terminal_evidence`, and `claimed_outbox_result_evidence`
- `weft/commands/types.py::TaskSnapshot` and the distinct richer snapshot
  currently defined in `system.py`
- every caller of `_collect_task_snapshot_records`
- the status tests covering runtime conflict, stale liveness, internal-service
  supersession, manager supersession, activity, and claimed outbox evidence

### Exact target seam

Create these internal contracts in `_task_snapshot_reducer.py`:

```python
@dataclass(frozen=True, slots=True)
class FoldedTaskRecord:
    """Immutable result of reducing ordered log events for one TID."""

    tid: str
    tid_short: str
    name: str
    status: str
    event: str
    activity: str | None
    waiting_on: str | None
    started_at: int | None
    completed_at: int | None
    return_code: int | None
    error: str | None
    last_timestamp: int
    taskspec_payload: dict[str, Any] | None
    metadata: dict[str, Any]
    event_payload: dict[str, Any] | None
    runner_diagnostics: dict[str, Any] | None
    status_reason: str | None


@dataclass(frozen=True, slots=True)
class SnapshotDraft:
    """Policy state after lifecycle and task-local evidence, before probes."""

    record: FoldedTaskRecord
    lifecycle_status: str
    public_status: str
    local_evidence: TaskEvidenceSnapshot | None


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    """Host/runtime liveness result; absence means the probe was not run."""

    live: bool
    evidence: str
    strength: str


@dataclass(frozen=True, slots=True)
class SnapshotProbePlan:
    """Pure policy decision made after stale-liveness classification."""

    draft: SnapshotDraft
    stale_liveness_reason: str | None
    provisional_public_status: str
    acquire_runtime_observation: bool
    acquire_claimed_outbox: bool


@dataclass(frozen=True, slots=True)
class SnapshotEvidence:
    """All I/O observations acquired outside final reduction."""

    resolved_runtime_entry: Mapping[str, Any] | None
    runtime_handle: RunnerHandle | None
    runtime_description: Mapping[str, Any] | None
    runtime_observation: RuntimeObservation | None
    claimed_outbox: TaskEvidenceSnapshot | None
    active_service_tid: str | None
    selected_active_manager_tid: str | None


def reduce_task_event(
    current: FoldedTaskRecord | None,
    payload: Mapping[str, Any],
    timestamp: int,
    *,
    tid_filters: AbstractSet[str] | None,
) -> FoldedTaskRecord | None:
    """Apply one already-read event without I/O or shared mutation."""


def prepare_snapshot(
    record: FoldedTaskRecord,
    *,
    local_evidence: TaskEvidenceSnapshot | None,
) -> SnapshotDraft:
    """Apply lifecycle and local-evidence precedence before external probes."""


def plan_snapshot_probes(
    draft: SnapshotDraft,
    *,
    stale_liveness_reason: str | None,
) -> SnapshotProbePlan:
    """Choose provisional status and the remaining probes without I/O."""


def reduce_task_snapshot(
    probe_plan: SnapshotProbePlan,
    evidence: SnapshotEvidence,
    *,
    now_ns: int,
) -> CollectedTaskSnapshot | None:
    """Apply MF-5 precedence and build one public snapshot."""


def order_task_snapshots(
    records: Iterable[CollectedTaskSnapshot],
    *,
    include_terminal: bool,
) -> list[CollectedTaskSnapshot]:
    """Apply the existing terminal filter and stable ordering."""
```

Use `collections.abc` annotations. The collector owns the `dict` keyed by TID
and replaces one immutable `FoldedTaskRecord` per accepted event. This keeps
memory O(distinct TIDs) and ensures lazy queue iteration occurs in
`system.py`, not inside a function falsely called pure. Nested TaskSpec,
metadata, diagnostics, and event payloads remain copied dictionaries because
their schemas are external inputs. Evidence dataclasses carry observations,
not callbacks or a context.

Keep in `system.py`:

- queue creation/closing and `_iter_log_events`
- `time.time_ns()`
- service-registry collection and `_InternalServiceOwnerEvidenceIndex`
- active-manager selection and TID-mapping reads
- `_merge_runtime_entry`: copy event payload runtime fields first, then overlay
  the latest TID-mapping entry so mapping values win exactly as they do today
- runner plugin lookup and runtime description
- host-process liveness probes and conversion to `RuntimeObservation`
- `task_local_terminal_evidence` and claimed-outbox queue probes
- construction of `SnapshotEvidence`

Evidence acquisition must preserve the current conditional probe order. In
particular:

1. merge runtime metadata, resolve runner/handle, and describe the runtime;
2. inspect task-local terminal evidence only for a nonterminal lifecycle row;
3. call `prepare_snapshot`;
4. when local evidence did not make the first branch terminal, compute the
   existing stale-liveness reason and active service owner;
5. call pure `plan_snapshot_probes`. It alone maps the two internal-service
   stale reasons to provisional `failed`, decides whether that provisional
   status requires a runtime observation, and applies the current
   claimed-residue condition;
6. acquire `RuntimeObservation` only when the plan requests it; absence means
   “not observed,” never “observed dead”;
7. acquire claimed-outbox evidence only when the plan requests it. The planner
   must skip it for a terminal lifecycle row and when local evidence supplies
   its own reconciliation; otherwise it requests the probe, even when
   task-local terminal evidence without reconciliation was found.

The last rule is counterintuitive but is current behavior. Do not “simplify”
it in this refactor. Add a characterization case before deciding separately
whether [MF-5] should change.

Move into the reducer, because they are pure and coupled to snapshot policy:

- `_reconcile_lifecycle_status`
- `_reconciliation_diagnostic`
- `_stale_liveness_reconciliation`
- `_superseded_manager_reconciliation`
- event-fold rules now embedded in the collector
- duration, terminal activity clearing, final construction/filter/order

The moved `_reconciliation_diagnostic` must consume
`RuntimeObservation | None`. It must not call `handle_has_live_host_process`
or any other OS probe. `runtime_handle` is carried separately only so the
public snapshot can serialize the already acquired handle.

Keep `_stale_liveness_reason` in `system.py` in this slice. It calls runtime
process probes and the service-owner index, so moving it would either make the
pure reducer impure or force a false abstraction over liveness. Pass its
already-computed reason and active service TID through `SnapshotEvidence`.

`CollectedTaskSnapshot` and the richer current `system.TaskSnapshot` may move
to the reducer module so the reducer can construct them directly, but
`system.py` must re-export both names for its existing internal callers. Do not
merge the richer snapshot with `weft.commands.types.TaskSnapshot` in this
slice; those types serve different compatibility paths and consolidation would
widen the change without reducing the collector's complexity.

### Comprehension questions

Before editing, the implementer must answer:

1. Why may a later nonterminal event not overwrite a terminal record?
2. Why can terminal local evidence change fields while runner diagnostics
   alone cannot select public lifecycle state?
3. When does claimed outbox residue produce
   `claimed_result_without_terminal`, and why is it not readable-result proof?
4. Why can an old manager row become public `failed` while generic stale host
   liveness remains only reconciliation?
5. Which operations in the current collector can open a queue, inspect a
   process, invoke a runner plugin, or read time?

## 4. Invariants and Constraints

Preserve exactly:

- terminal lifecycle proof never regresses to a nonterminal state
- `task_activity` updates activity/waiting state but does not fabricate a
  TaskSpec-bearing record
- terminal status clears `activity` and `waiting_on`
- evidence priority and all classification/reason strings named in [MF-5]
- local evidence overrides for status, return code, error, completion
  observation, and last timestamp
- `claimed_result_without_terminal` does not set `completed_at` from its
  observation
- duration uses the injected `now_ns`, is nonnegative, and preserves current
  handling of missing timestamps
- manager and internal-service supersession behavior
- `include_terminal`, short/full TID filtering, and ordering with
  `running`/`spawning` first then TID
- `TaskSnapshot.to_dict()` omission of absent optional diagnostic fields
- current callers and import paths for `system.TaskSnapshot`,
  `system.CollectedTaskSnapshot`, and `_collect_task_snapshot_records`

Not allowed:

- a state-machine framework, policy registry, callback-based reducer, or new
  dependency
- moving queue/process/plugin access into the reducer
- changing evidence priority to make the code look simpler
- mocking the reducer's inputs in command integration tests when real
  isolated queues can create the observation
- splitting Manager, BaseTask, service collection, or unrelated system
  commands
- renaming public functions or changing JSON output

Rollback is one code-unit revert: restore the collector body and its pure
helpers together, remove the reducer module/tests, and retain the
characterization tests. No persisted data, deployment order, or migration is
involved.

## 4a. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| Verification §7 / [MF-5] | Run C901 against both `system.py` and the reducer as a zero-error command. | The reducer and `_collect_task_snapshot_records` have no C901 violations. A whole-file `system.py` C901 invocation still reports three unrelated pre-existing functions: `_stale_liveness_reason`, `_collect_internal_service_snapshots`, and `_watch_task_events`. | Expanding this behavior-preserving snapshot slice into three unrelated system-command refactors would violate scope. The targeted reducer gate plus absence of a collector finding proves the planned complexity result. | Closed as a verification-command correction; no spec behavior changes. |

## 4b. Spec Baseline

- `a692c08becd6db2d8c0672828ce487dc10b08354` —
  `docs/specifications/05-Message_Flow_and_State.md` and
  `docs/specifications/09-Implementation_Plan.md`
- Plan type: behavior-preserving structural refactor; no proposed behavior
  delta

## 5. Implementation Tasks

### Task 1 — Freeze characterization before extraction

- Add table-driven tests around the current collector using isolated
  SimpleBroker queues and fixed `now_ns`.
- At minimum fire these distinct contracts:
  1. nonterminal event followed by terminal then stale nonterminal;
  2. activity update then terminal clearing;
  3. contradictory terminal event/status with live and absent runtime;
  4. readable terminal local evidence and claimed-outbox residue;
  5. stale host process, runtime-less stale task, internal-service
     supersession, and active-manager supersession;
  6. short/full TID filters, `include_terminal=False`, and result ordering.
- Assert full `CollectedTaskSnapshot.snapshot.to_dict()` values for compact
  fixtures, not merely `status`.
- Reuse the real queue/context fixture path in `tests/commands/test_status.py`.
  Patch only nondeterministic OS/runner liveness at its narrow boundary when a
  real child process would make the test slow or flaky.
- Demonstrate the tests fail when terminal-regression protection, claimed
  classification, active-manager supersession, a conditional probe guard, or
  mapping-over-event precedence is deliberately inverted.

### Task 2 — Extract the ordered event fold

- Introduce `FoldedTaskRecord` and `reduce_task_event` with the signatures
  above.
- Preserve input order and exact existing update semantics. Do not sort events
  or materialize the generator; SimpleBroker timestamps and
  `_iter_log_events` already define replay order. The `system.py` loop calls
  `reduce_task_event` once per decoded row and replaces the returned per-TID
  value in its accumulator.
- Unit-test malformed/missing TID, activity-only unknown TID, non-dict
  TaskSpec, terminal regression, timestamp replacement, metadata copying, and
  both short/full TID filters with literal event dictionaries.
- Replace the collector's inline fold with that per-row transition. Keep lazy
  iteration and queue closure in the existing `system.py` `finally`.

### Task 3 — Extract per-record pure reduction

- Introduce `SnapshotEvidence` and `reduce_task_snapshot`.
- Move only the pure policy helpers listed in §3. Leave liveness acquisition
  and service-owner lookup in `system.py`.
- In `system.py`, add one narrowly named evidence-acquisition helper:

  ```python
  def _collect_snapshot_evidence(
      ctx: WeftContext,
      record: FoldedTaskRecord,
      *,
      mapping_entry: Mapping[str, Any] | None,
      selected_active_manager_tid: str | None,
      service_owner_index: _InternalServiceOwnerEvidenceIndex,
      now_ns: int,
  ) -> tuple[SnapshotProbePlan, SnapshotEvidence]: ...
  ```

  It performs the current queue/runtime probes in the seven-stage order in §3.
  It calls pure `prepare_snapshot` and `plan_snapshot_probes`, then performs
  only the probes requested by the plan. It must not duplicate stale-reason,
  diagnostic-status, or claimed-probe policy. It owns `_merge_runtime_entry`,
  with event data copied first and the TID mapping overlaid second.
- Unit-test `reduce_task_snapshot` with concrete dataclass values for every
  precedence branch. Do not use `Mock`; construct `TaskEvidenceSnapshot` and
  `RunnerHandle` values directly.
- Characterize a runtime handle present only in the event payload and a
  conflicting event/mapping pair where the mapping value wins.
- Add acquisition-boundary sentinels that prove:
  1. terminal lifecycle rows call neither task-local nor claimed-outbox probes;
  2. local evidence with its own reconciliation suppresses claimed probing;
  3. local terminal evidence without reconciliation retains the current
     claimed probe;
  4. ordinary nonterminal rows do not acquire terminal
     runtime-reconciliation observations; and
  5. terminal diagnostic rows do acquire them.
  These sentinels may patch the named I/O functions to record or forbid calls;
  they use real `FoldedTaskRecord` and evidence values and do not mock the
  reducer.
- Preserve a single `TaskSnapshot` construction site.

### Task 4 — Make the collector an orchestration statement

- Reduce `_collect_task_snapshot_records` to: resolve fixed inputs; open and
  close the log queue; fold events; acquire evidence per record; reduce; filter
  and order.
- Call `order_task_snapshots` once. Do not retain parallel inline filtering or
  sorting paths.
- Add a source-level architecture assertion that the reducer module does not
  import `WeftContext`, `Queue`, `time`, runner plugin loaders, or command
  `system`, and does not call `handle_has_live_host_process` or `pid_is_live`.
  This is a boundary guard, not the primary behavioral test.
- Run Ruff's C901 check for the named collector and reducer functions. The goal
  is no C901 violation under the repository threshold, not an arbitrary line
  count. If a pure function still exceeds the threshold, split by a real
  named policy phase, never by numbered helper or callback dispatch.

### Task 5 — Synchronize ownership documentation

- Add the reducer module to [MF-5]'s implementation mapping while retaining
  `system.py` as I/O/orchestration owner.
- Update both module docstrings with [MF-5] ownership.
- Add the plan backlink beside the [MF-5] implementation mapping.
- Close every Deviation Log row before implementation completion.

## 6. Testing Plan

```text
REAL QUEUE CHARACTERIZATION
  task-log + TID mapping + task-local evidence
    -> collector
    -> exact public snapshot

PURE EVENT FOLD
  ordered literal payloads
    -> FoldedTaskRecord

PURE POLICY MATRIX
  FoldedTaskRecord + SnapshotDraft/ProbePlan + concrete evidence + fixed now_ns
    -> exact CollectedTaskSnapshot

BOUNDARY
  reducer imports
    -> no context, queue, clock, process, or plugin I/O
```

The integration suite is the anti-drift oracle. Pure tests make every branch
cheap to fire; they do not replace real-queue tests. Avoid snapshot-file
goldens whose large diffs hide semantic changes. Prefer explicit expected
dataclasses/dictionaries per case.

## 7. Verification and Gates

Per task:

```bash
./.venv/bin/python -m pytest tests/commands/test_task_snapshot_reducer.py -q
./.venv/bin/python -m pytest tests/commands/test_status.py -q
./.venv/bin/ruff check weft/commands/system.py weft/commands/_task_snapshot_reducer.py --select C901
```

Final:

```bash
. ./.envrc
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check weft
./bin/check-doc-paths
./bin/check-dom15-fixtures
```

Review `git diff --stat` and `rg "_collect_task_snapshot_records"` to confirm
callers did not fork a second reconstruction path.

## 8. Independent Review Loop

Review is mandatory because this refactors the public lifecycle read model.
The reviewer must read [MF-5], the complete current collector and its policy
helpers, `TaskEvidenceSnapshot`, the proposed reducer module contract, and the
named status tests.

Review prompt:

> Answer PASS or BLOCKED. Does this plan extract a genuinely pure, cohesive
> reducer while leaving all queue, clock, process, manager-selection, service
> registry, and runner-plugin I/O in `system.py`? Can a zero-context engineer
> preserve every [MF-5] priority and diagnostic branch? Identify any false seam,
> missing characterization case, type ambiguity, or mock-heavy proof.

Accepted changes receive a scoped round-2 verification by the same reviewer.

## 9. Out of Scope

- Changing [MF-5] behavior or evidence priority
- Combining public and internal snapshot dataclasses
- Moving command reconstruction policy into `core`
- Redesigning service evidence, task evidence, or runner liveness
- Splitting `system.py` commands unrelated to task snapshot reconstruction
- Converter deduplication, import cycles, validation layering, and test
  integrity covered by the other independent plans
- Broad naming cleanup

## 10. Fresh-Eyes Review

Completed 2026-07-29 against the draft and current source:

- Replaced the lazy-iterable “pure fold” with an immutable per-event
  transition called inside the system-owned queue loop, retaining O(TIDs)
  replay memory.
- Enumerated every folded field and replaced placeholder evidence parameters
  with exact staged dataclasses and a concrete acquisition signature.
- Separated “runtime not observed” from “observed not live.”
- Assigned runtime merge ownership and pinned event-first, mapping-wins
  precedence.
- Added a second pure probe-planning phase after stale classification so the
  internal-service-to-`failed` rule is not copied into the I/O helper.
- Pinned the counterintuitive existing claimed-residue condition and added
  call-boundary sentinels for every conditional probe.
- Kept the richer internal snapshot distinct from
  `weft.commands.types.TaskSnapshot`.

## 11. Independent Review Result

Reviewer: `dup_complexity` (independent agent), 2026-07-29.

Round 1: **BLOCKED** on four issues: a queue-backed lazy iterable inside the
purported pure fold; no unobserved runtime state; ambiguous runtime merge
ownership/types; and tests that did not prove conditional I/O.

Round 2: **FAIL** on one remaining issue. The stale internal-service branch
could select provisional `failed` only by duplicating policy in the I/O helper.

Rounds 1–2 were corrected through the per-event transition,
`SnapshotDraft`, `RuntimeObservation`, `SnapshotProbePlan`, exact merge
contract, and acquisition sentinels.

Round 3: **PASS**. The same reviewer verified the remaining probe-planning
correction and reported no new material defect.

## 12. Implementation Result

Completed 2026-07-29.

- Extracted the ordered event fold, staged probe policy, final reconciliation,
  snapshot construction, filtering, and ordering into the pure
  `weft/commands/_task_snapshot_reducer.py` module.
- Reduced `_collect_task_snapshot_records` to queue replay, evidence
  acquisition, reduction, and ordering. Queue, clock, process, manager,
  service-registry, and runner-plugin I/O remain in `system.py`.
- Kept `TaskSnapshot`, `CollectedTaskSnapshot`, and
  `_runner_name_for_snapshot` compatibility paths available from
  `weft.commands.system`.
- Added direct policy tests, real-queue collector tests, conditional-probe
  sentinels, exact payload assertions, and a source-level purity guard.
- Mutation proof caught deliberate inversions of terminal-regression
  protection, claimed-result probing, manager supersession, local-evidence
  probe guarding, and mapping-over-event precedence.

Implementation review:

- Grok round 1: **PASS**, with non-blocking observations about a duplicated
  service-key policy and a missing function separator.
- The service-key policy was consolidated in the reducer, the dead system copy
  was removed, and formatting was corrected.
- Grok round 2: **PASS**. The reviewer confirmed behavior equivalence and the
  pure boundary after consolidation.

Verification:

- focused snapshot, status, and task-evidence suites: 83 passed
- full suite: 2442 passed, 14 skipped
- mypy: 195 source files, no issues
- Ruff: passed
- reducer C901 gate: passed; the collector has no C901 finding. The three
  unrelated pre-existing `system.py` findings are recorded in §4a.
- DOM-15 fixture contract: passed
- documentation path check: unchanged repository baseline of eight dangling
  claims, none introduced or touched by this plan

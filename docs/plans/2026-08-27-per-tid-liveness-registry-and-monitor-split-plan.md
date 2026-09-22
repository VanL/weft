# Per-TID Liveness Registry and Monitor Split Plan

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5], Cleanup Boundary; docs/specifications/07-System_Invariants.md [OBS.6], [OBS.6a], [OBS.13.7]; docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.4]; docs/specifications/00-Quick_Reference.md (queue table)
Superseded by: [2026-08-29-liveness-monitor-plan.md](./2026-08-29-liveness-monitor-plan.md)

Class: 5 — spec-changing, with risky triggers (queue-name contract change,
cleanup-lifecycle change, destructive-cleanup custody move, durable-spine
publication path). The hardening-plans checklist applies. Plan type:
implementation with spec revision. Promotion strategy: A — in-file edits,
text before link claims.

## Design Compass

Read this section before reviewing or implementing, and return to it whenever
a decision point appears. The design target is the simplest, most consistent,
most Unix-like shape that preserves correctness:

- **The mental model is `/proc`.** `weft.state.tid_mappings.<tid>` is
  `/proc/<pid>/status` for tasks: one namespace entry per task, listing the
  namespace enumerates tasks, peeking an entry reads current state, and the
  entry is removed when its owner is provably gone. No new concepts — the
  queue namespace does what namespaces already do in this codebase
  (`T{tid}.inbox` et al.).
- **State is evidence, not property.** Every `weft.state.*` surface must be
  deletable at rest and rebuilt by ordinary runtime publication. This plan
  adds that as an invariant ([OBS.18]) and relies on it: there is no data
  migration, only a one-time legacy cleanup.
- **Deliberately not built** (each was considered and rejected as ceremony
  that does not improve correctness): a death-observations/verdict queue
  (per-TID peeks are already cheap; one shared probe function suffices),
  payload slimming (`activity` eviction, `started` rename — severable, out
  of scope), and a registered-runner-probe destruction rule (contract
  amendment deferred; undecidable-means-live is unchanged here). A
  newest-first peek was originally on this list as a hypothetical upstream
  addition; SimpleBroker 8.0.0 (released 2026-08-28, weft floors already
  raised) shipped it as public `order="newest"` bounded selection, so
  readers use it directly — note it replaces only the read idiom, not the
  self-trim discipline, which still bounds accumulation.
- **Review stance**: every proposed change to this plan must claim a concrete
  correctness improvement or the removal of one. Additions that reify
  process — extra states, extra queues, extra config, extra gates without a
  named failure they prevent — are findings *against* the plan, not for it.

## 1. Goal

Two coordinated changes:

1. Replace the single flat `weft.state.tid_mappings` queue with a per-task
   namespace `weft.state.tid_mappings.<tid>` holding one current runtime
   snapshot per task (append + owner-local self-trim). "Newest per key"
   moves from message space into the queue namespace; the two-pass
   reachability scan, full-queue newest-ID evidence pass, superseded-row
   classification, and catch-up waypoint machinery for this queue are
   removed. Retention becomes: delete a mapping queue whose newest row is
   proven dead and old enough; trim crash-residue rows on sight.
2. Split the current TaskMonitor into two supervised services: a new
   **LogMonitor** that owns everything `weft.log.tasks` (collation store,
   family summaries, external JSONL sink, deferred writes, and all
   destructive custody of task-family queues), and the existing
   **TaskMonitor**, which becomes the liveness monitor — it owns the
   `weft.state.tid_mappings.*` namespace, the shared liveness probe, and
   runs on a faster cadence.

No data migration: runtime state is rebuildable by construction ([OBS.18]).
Cutover is deploy, one-time legacy-queue delete, and a bounded grace window
for destructive dispositions.

## 2. Source Documents

Source specs (exact sections):

- `docs/specifications/05-Message_Flow_and_State.md` — Cleanup Boundary
  (tid-mappings policy paragraph, cleanup owners list), [MF-5]
- `docs/specifications/07-System_Invariants.md` — [OBS.6], [OBS.6a],
  [OBS.13.7], Implementation mapping block above [OBS.1]
- `docs/specifications/01-Core_Components.md` — [CC-2.2] responsibilities
  list, [CC-2.4] terminal-hint bullet
- `docs/specifications/00-Quick_Reference.md` — global queue table and notes

Related plans:

- `docs/plans/2026-08-25-bounded-tid-mapping-publication-plan.md` — draft,
  not implemented. **This plan supersedes it** (Task 9). Inherited from it:
  the blind edge-triggered append contract (absorbed into [OBS.6a] here; the
  per-TID writer performs no mapping-history read, which is that plan's core
  defect fix), the owner-local last-written guard (reused as the self-trim
  pointer), and the forced-terminal-publication `terminal` contract already
  landed in `0bf1ddd`. Its two-pass retention corrections and capacity
  gates address machinery this plan deletes; they are not inherited.
- `docs/plans/2026-08-25-manager-admission-control-plan.md` — landed
  (`a659a48`, `0bf1ddd`). Admission consumes the latest-per-TID mapping
  reduction plus `mapping_row_is_live`; its read sites are migrated in
  Task 4.

Guidance: `docs/agent-context/decision-hierarchy.md` [DOM-15],
`docs/agent-context/runbooks/writing-plans.md`,
`docs/agent-context/runbooks/hardening-plans.md`,
`docs/agent-context/engineering-principles.md`, `CLAUDE.md` §1.1, §4.

## 3. Context and Key Files

Current structure (what exists today):

- `weft/core/tasks/base.py` — `_register_tid_state()` (single best-effort
  append to the flat queue), `_build_tid_state_payload()`,
  `register_managed_pid()`, `register_runtime_handle()`, `_set_activity()`.
  Publication is edge-triggered at five call sites (construction, terminal
  latch in `_report_state_change`, new PID, changed handle, changed
  activity).
- `weft/core/monitor/task_monitor.py` — one service owning task-log
  collation, external sink, destructive cleanup slices, liveness reads
  (`_active_runtime_tids`, `_destruction_protected_runtime_tids`), and the
  tid-mapping cleanup cycle (`_run_task_monitor_cleanup_cycle`,
  `exclude_tids=(self.tid,)`).
- `weft/core/monitor/policies/tid_mapping.py` — keep-newest-per-key +
  liveness gate, `mapping_row_is_live` (the shared probe; its docstring
  forbids duplication), windowed and streaming selectors. Most of this file
  is deleted by Task 5; **`mapping_row_is_live` survives** and moves to the
  shared liveness module.
- `weft/core/monitor/cleanup.py` — `run_task_monitor_cleanup`,
  `_newest_tid_mapping_ids` (full-queue pass one). The tid-mapping branch
  is deleted by Task 5; the task-log branch moves to LogMonitor in Task 6.
- `weft/core/manager.py` — `_build_task_monitor_spawn_payload`,
  `_build_heartbeat_spawn_payload`; internal-service supervision (`ensure`
  lifecycle, `INTERNAL_SERVICE_KEY_TASK_MONITOR`).
- `weft/_constants.py` — `WEFT_TID_MAPPINGS_QUEUE`, monitor policy names,
  `TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS`,
  `INTERNAL_SERVICE_KEY_*`.
- Other flat-queue readers to migrate (Task 4): `weft/core/endpoints.py`,
  `weft/core/pruning/runtime.py`, `weft/commands/system.py`, the Manager
  admission observer, and monitor liveness reads.
- SimpleBroker facts verified against `../simplebroker` source at 8.0.0
  (weft's floor since the completed
  `2026-08-28-simplebroker-8-upgrade-plan.md`): queues are virtual rows in
  one `messages` table; `list_queues(prefix=...)` is an index-range
  `SELECT DISTINCT`; `peek` defaults to oldest-first and v8 adds public
  bounded newest-first selection (`order="newest"` in Python,
  `--newest` in the CLI; `--newest --all` is invalid — always pass a
  bounded limit); `delete(queue)` is one indexed statement;
  `delete_message_ids(queue, ids)` exists for exact-row deletes; queue
  names allow dots, max length 512. v8's SQL schema v6 removed the private
  `id` column — the public message ID (timestamp) is the only row
  identifier, which is what this plan means everywhere it says
  "greatest message ID".

Read first (with comprehension checks):

- `docs/specifications/07-System_Invariants.md` [OBS.6], [OBS.6a],
  [OBS.13.7] — Q: why must destruction evidence be decidable from the row
  payload alone, and what does "undecidable means live" require when a
  handle has no probeable host PIDs? Q: why is writer-side read-before-write
  deduplication forbidden?
- `docs/specifications/05-Message_Flow_and_State.md` Cleanup Boundary —
  Q: which owner clears `T{tid}.ctrl_*` on clean exit, and why does the
  monitor's destructive slice still exist?
- `weft/core/monitor/policies/tid_mapping.py` `mapping_row_is_live`
  docstring — Q: which two callers share this probe today?
- `tests/helpers/weft_harness.py` — harness lifecycle and cleanup rules.

Shared paths — do not duplicate:

- `mapping_row_is_live` remains the only liveness probe (new home:
  Task 4's shared module). Do not fork probe logic into LogMonitor.
- Exact-row deletion goes through the existing pruning apply path
  (`weft/core/pruning/apply.py`) or the SimpleBroker exact-delete surface
  weft already uses — no new deletion helper family.
- Manager internal-service supervision: reuse the heartbeat/task-monitor
  spawn-payload pattern for LogMonitor; do not invent a second supervision
  path.

## 4. Invariants and Constraints

Must not change:

- TID format/immutability; forward-only state transitions; `spec`/`io`
  immutability; reserved-queue policy semantics.
- `weft.log.tasks` contract: full redacted TaskSpec per event, collation
  before deletion, durable-before-delete in `jsonl_then_delete`. This plan
  moves custody, not behavior.
- Undecidable-means-live: destruction requires disproof carried in the row
  payload (`terminal: true`, or all scoped `(pid, create_time)` pairs dead).
  A probe error or absent probe target is protection, never license.
- [OBS.6a] blind append: the writer never reads mapping history and never
  compares payloads queue-side. Self-trim uses only the owner-local
  remembered id of the writer's own previous append.
- Mapping publication stays best-effort: a broker failure must not mutate
  lifecycle state or abort the remaining lifecycle publication path.
- `weft.state.*` stays out of dumps; the per-TID namespace inherits the
  existing prefix exclusion (verify, don't assume — Task 3 test).
- Layer boundary: no SimpleBroker changes. The design works with released
  SimpleBroker surfaces only (verified above).

Hidden couplings (name them before decomposition):

- Destruction-protection is consumed by stale_open disposal, dead-task queue
  cleanup, and reserved cleanup — after the split these live in LogMonitor
  but read TaskMonitor-owned state. The coupling is the shared liveness
  module reading the namespace directly; there is no cross-service RPC.
- Manager admission reads latest-per-TID mappings on the spawn path;
  slowing that read slows spawns. Task 1's benchmark covers the admission
  sweep shape explicitly.
- Absence of a mapping queue is treated by destruction paths as absence of
  protection. The cutover grace window (Task 8) and [OBS.18]'s
  wipe-degradation clause bound this; quiet live tasks republish only on
  edges.
- `_queue(WEFT_TID_MAPPINGS_QUEUE)` handles are cached per task
  (`base.py` queue caching); the per-TID name is constant for a task's
  life, so caching still works — but the monitor's enumeration must list
  queues fresh each cycle, never cache the namespace.

Review gates for this plan: no second publication path (the per-TID writer
replaces the flat writer in the same release; no dual-write mode); no new
dependency; no mock-heavy substitute for broker-backed proofs; independent
review of plan + delta before the spec-promotion slice; review findings are
weighed per the Design Compass (correctness improvement vs. process
reification).

Fatal vs best-effort: mapping append and self-trim are best-effort
observability; retention deletion errors are logged and retried next cycle
(never block the monitor loop); LogMonitor collation-store failures keep
their existing fatal/degraded semantics unchanged.

One-way doors: deleting the legacy flat queue is destructive but low-risk
(ephemeral, rebuildable); the queue-name contract change is the real
one-way door — old readers cannot read new state. Mitigation: single
coordinated release (no mixed-version operation), and rollback (below)
restores the old contract with the same wipe-and-rebuild property.

Rollback: revert the release. Old code recreates the flat queue from live
tasks' next edges — the same [OBS.18] property that makes forward cutover
migration-free makes rollback migration-free. The per-TID namespace left
behind is inert garbage; delete it with `weft queue delete` per queue or
leave it for manual tidy. No compatibility window is required in either
direction. Rollback of the split alone: the Manager stops spawning
LogMonitor and the pre-split TaskMonitor resumes both roles (the extraction
in Task 6 must keep the moved code importable as one unit to keep this
cheap until the plan completes).

Rollout sequencing: Tasks are dependency-ordered below; the split (Task 6)
lands after the per-TID rework (Tasks 3–5) so the extraction moves already-
simplified code. Ship as one release after Task 8.

## Spec Baseline

- `33e1ab7` — docs/specifications/00-Quick_Reference.md,
  01-Core_Components.md, 05-Message_Flow_and_State.md,
  07-System_Invariants.md at plan authoring time.
- Coordination note (hard precondition for Task 2): the worktree at
  authoring time carries uncommitted edits to 01/05/07,
  `weft/core/monitor/policies/tid_mapping.py`, `weft/core/monitor/cleanup.py`,
  `weft/core/tasks/base.py`, and tests belonging to the in-flight
  bounded-publication implementation. At the committed baseline `33e1ab7`,
  [OBS.6a] does not yet exist and [OBS.6] is a one-liner; parts of the
  "current structure" described in §3 (streaming selectors, waypoint
  machinery, the full OBS.6/OBS.6a prose) exist only in that uncommitted
  state. Task 2 must not begin until that in-flight state has either
  **landed** (then rebase this delta onto the landed text — the [OBS.6a]
  delta here is a full replacement precisely so it applies cleanly) or been
  **explicitly folded or abandoned** by the owner of that work (then the
  [OBS.6a] delta inserts as a new invariant, and Task 5's deletion targets
  are whatever of the named machinery actually exists at that point). Do
  not checkout/revert those shared dirty files; coordinate with their
  owner.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| 00-Quick_Reference.md | A | queue table row + notes |
| 07-System_Invariants.md | A | [OBS.6], [OBS.6a], new [OBS.18], [OBS.13.7] pointer |
| 05-Message_Flow_and_State.md | A | Cleanup Boundary owners + tid-mappings paragraph |
| 01-Core_Components.md | A | [CC-2.2] bullet, [CC-2.4] pointer |

[OBS.18] is the next unused OBS number at baseline; re-verify at promotion
and renumber if another landing claimed it.

### 00-Quick_Reference.md — queue table row (replace the `weft.state.tid_mappings` row)

> | `weft.state.tid_mappings.<tid>` | Per-task runtime registry entry: current identity + liveness snapshot | No (runtime state) |

### 00-Quick_Reference.md — notes (replace the tid_mappings note)

> - `weft.state.tid_mappings.<tid>` is the per-task runtime registry: one
>   queue per task holding that task's current snapshot (append plus
>   owner-local trim of the writer's own previous row). Think `/proc`:
>   listing the `weft.state.tid_mappings.` prefix enumerates tasks, peeking
>   an entry reads current state (more than one row is crash residue —
>   readers take the greatest message ID), and the liveness monitor deletes
>   an entry once its snapshot is provably dead and old enough. Payloads
>   include the additive `terminal: bool` liveness hint; positive scoped
>   host-process liveness wins, otherwise `terminal: true` releases the
>   entry. See [CC-2.4], [OBS.6], [OBS.13.7], [OBS.18].

### 07-System_Invariants.md — [OBS.6] (replace)

> - **OBS.6**: Each task owns one runtime-registry queue,
>   `weft.state.tid_mappings.<full-tid>`, holding the task's current
>   complete runtime-observability snapshot. The queue namespace is the
>   registry index: enumerating tasks is a prefix listing of queue names,
>   and short→full TID resolution is a name operation requiring no message
>   reads. A registry queue normally holds one row; multiple rows are valid
>   transient crash residue, and a consumer that requires current state
>   selects the row with the greatest broker message ID. Uniqueness is not
>   a queue invariant; convergence back to one row is the liveness
>   monitor's job.

### 07-System_Invariants.md — [OBS.6a] (full replacement text — self-contained so it applies whether or not the in-flight bounded-plan worktree text has landed; if no [OBS.6a] exists at promotion time, insert this as a new invariant after [OBS.6])

> - **OBS.6a**: Registry publication is edge-triggered: every write is a new
>   fact — a state transition or a report — so a producer appends its
>   complete current snapshot to its own registry queue without reading
>   registry history and without any writer-side payload comparison, then
>   best-effort deletes exactly its own previous row using the owner-local
>   remembered message ID of its last successful append (append first, trim
>   second; a failed trim leaves benign residue for the liveness monitor).
>   Publication work is therefore independent of registry-history depth.
>   Edge detection lives at the call sites, each of which fires only when
>   the owner-local state it governs actually changed (unchanged activity,
>   an equal runtime handle, and a known managed PID are suppressed at
>   their call sites) or when the write is itself a report (construction,
>   restart republish). The terminal transition publishes its snapshot
>   exactly once per task, latched only on a successful write so a failed
>   terminal append retries on the next terminal report. Queue-wide
>   read-before-write deduplication and writer-side equivalence oracles are
>   forbidden: a curated comparator field list silently suppresses newly
>   added payload fields, and a report of identical values is still a new
>   observation. A broker failure from the registry append or trim is a
>   best-effort observability failure: it must not mutate lifecycle state,
>   replace task-owned lifecycle evidence, or abort the remaining lifecycle
>   publication path. Payload construction and serialization defects are
>   not broker failures and remain visible internal errors.

### 07-System_Invariants.md — [OBS.13.7] evidence-model passage (targeted replacement: the passage from "that additionally protects every TID whose newest `weft.state.tid_mappings` row" through "or a family with no mapping row at all grants no protection." becomes:)

> that additionally protects every TID whose registry queue's newest row
> the liveness monitor's own probe (`mapping_row_is_live`, in
> `weft/core/liveness.py`) retains. A non-terminal snapshot with no runtime
> handle, or a handle with no probeable host PIDs (e.g. an
> external/container runner), is undecidable and therefore protected. A
> valid `terminal: true` snapshot without positive scoped host-process
> proof, a newest row whose probeable host processes are all dead, or a
> TID with no registry queue at all grants no protection.

(The remainder of [OBS.13.7] stands; the promotion slice also updates its
other in-passage mentions of "newest mapping row"/"tid-mapping policy" to
registry-queue wording where the delta's grep sweep reaches them, and its
`weft/core/monitor/policies/tid_mapping.py` pointer to
`weft/core/liveness.py`.)

### 07-System_Invariants.md — new invariant, appended after [OBS.17]

> - **OBS.18**: Every `weft.state.*` queue is rebuildable evidence: it must
>   be safely deletable at rest, with ordinary runtime publication
>   repopulating it, and no durable behavior may depend on its history. A
>   proposed runtime-state surface that could not survive deletion is
>   misplaced and belongs in a durable queue instead. Deleting liveness
>   evidence degrades protection until owners republish (edge-triggered
>   writers republish on their next edge, not on a timer), so destructive
>   dispositions that treat absent evidence as absence of protection must
>   honor a bounded grace window after a known wipe.

Promotion gate for [OBS.18]: this invariant quantifies over every
`weft.state.*` surface, but this plan verifies rebuildability only for the
registry namespace. Task 2 therefore includes a recorded audit of the
republication path for each existing surface (`weft.state.services`,
`weft.state.streaming`, `weft.state.endpoints`, `weft.state.pipelines`)
before this text lands. If any surface cannot rebuild from ordinary runtime
publication, scope the invariant's first sentence to the surfaces that can,
record the exception and its owner in the invariant text itself, and add a
Deviation Log row — do not promote a universal claim the audit contradicts.

### 05-Message_Flow_and_State.md — Cleanup Boundary, cleanup owners (precise anchors)

Insert the following bullet immediately after the "task-owned cleanup may
remove spilled output" bullet:

> - monitor-owned cleanup is split between two supervised services: the
>   **LogMonitor** owns `weft.log.tasks` custody (collation, family
>   summaries, external JSONL, durable-before-delete, and all destructive
>   cleanup of task-family queues, gated on liveness evidence), and the
>   **TaskMonitor** is the liveness monitor — it owns the
>   `weft.state.tid_mappings.*` registry namespace, the shared payload
>   liveness probe, and runs on a faster cadence than log custody.

Then attribute the existing monitor bullets to their owning service: in the
"supervised task monitor additionally owns a default-on self-maintenance
pass" bullet the owner stays the TaskMonitor; in the "supervised task
monitor exists in the current contract. Its default `delete` mode…" bullet,
replace "the supervised task monitor" with "the supervised LogMonitor".
Finally, `grep -n "task monitor" docs/specifications/05-Message_Flow_and_State.md
docs/specifications/07-System_Invariants.md` and update custody
attributions in passages this delta touches (collation, external-log,
destructive dispositions → LogMonitor; liveness, registry retention →
TaskMonitor); list the lines changed in the promotion commit message. Do
not rewrite passages the delta does not touch.

### 05-Message_Flow_and_State.md — Cleanup Boundary (replace the flat-queue tid-mappings policy passage — the text from "Runtime-state queues such as `weft.state.tid_mappings` remain policy driven" through "hot-looping catch-up cycles with zero forward progress" — with the passage below, promoted to its **own bullet** in the owners list rather than nested inside the LogMonitor delete-mode bullet, since it describes TaskMonitor-owned policy:)

> Runtime-state queues remain policy driven. For the per-task registry
> namespace `weft.state.tid_mappings.<tid>`, the liveness monitor applies
> three rules per cycle, deleting exact observed message IDs in every case
> (an emptied queue vanishes on its own — SimpleBroker queues are virtual —
> so retiring a dead entry never requires a whole-queue delete that could
> clobber a row appended after the monitor's read): (1) within a registry
> queue, any row that is not the greatest-message-ID row is crash residue
> and may be deleted on sight; (2) a malformed row (invalid JSON, non-object
> payload, or a payload without the required registry shape) may be deleted
> on sight, exactly as malformed rows are deletable today; (3) the newest
> valid row may be deleted only when it is older than the configured
> minimum age and its own payload proves death — a valid task-owned
> `terminal: true` hint, or scoped `(pid, create_time)` host-process
> evidence that all probeable processes are dead. Undecidable payloads (no
> runtime handle, or no probeable host PIDs — e.g. external/non-host runner
> handles) are protected and retained: undecidable means skip, never
> delete, with accumulation bounded only by crash rate. There is no
> registered-probe destruction rule. The probe consults only evidence
> carried in the row payload itself — no task-log lookup and no
> collation-store reach. Registry queue names must carry a well-formed full
> TID suffix; consumers deriving TID sets from the namespace ignore
> malformed names, and the liveness monitor may report them as warnings.
> Because the namespace carries one queue per task, there is no
> newest-per-key scan, no superseded-row classification, and no cross-queue
> evidence pass: each deletion decision is local to one queue's rows.

### 01-Core_Components.md — [CC-2.2] responsibilities bullet (replace "append complete best-effort TID mapping snapshots without replaying shared mapping history, and maintain process titles")

> - publish the task's current runtime-registry snapshot to its own
>   `weft.state.tid_mappings.<tid>` queue — one best-effort append plus an
>   owner-local trim of the writer's own previous row ([OBS.6a]) — and
>   maintain process titles

### 01-Core_Components.md — [CC-2.4] (no text change; verify the terminal-hint bullet still reads correctly against the per-TID contract at promotion and update the `_Implementation mapping_` sentence in the linking slice, not the promotion slice)

## 5. Tasks

1. **Feasibility measurement (gate — run once, record, do not land).**
   - Outcome: recorded numbers for (a) `list_queues` prefix enumeration and
     (b) list + per-queue newest-row peek sweep at 1k and 10k synthetic
     TIDs, vs. the current single-queue full scan at equivalent row counts,
     on the SQLite backend.
   - Method: a throwaway script in the session scratchpad (not the repo
     tree) seeding a temp broker via real `Queue` writes. This is one-time
     plan acceptance evidence, not a regression test: nothing in ordinary
     development silently regresses an index-range listing, timing
     thresholds make brittle permanent tests, and acceptance evidence
     belongs in the plan record, not the tree. Do not create
     `tests/benchmarks/` (an earlier benchmark suite there was
     deliberately removed on 2026-08-27 for exactly this reason — it was
     the superseded bounded-publication plan's Task 1 gate evidence).
   - Read first: SimpleBroker `list_queues` and `peek` (paths in §3).
   - Done when: numbers are appended to this plan under a `## Benchmark
     Record` heading and the script is discarded. Stop and re-plan if the
     sweep is more than ~3× the current scan at 10k — that would break
     the admission-path coupling assumption.
2. **Spec-promotion slice (strategy A).**
   - Precondition: the Spec Baseline coordination note is resolved (the
     in-flight bounded-publication worktree state has landed or been
     explicitly folded/abandoned by its owner). Record which branch was
     taken here.
   - Pre-work: the [OBS.18] audit — read the publication paths for
     `weft.state.services`, `weft.state.streaming`, `weft.state.endpoints`,
     and `weft.state.pipelines` and record, per surface, whether ordinary
     runtime publication rebuilds it after deletion at rest. Scope the
     [OBS.18] text per its promotion gate if any surface fails.
   - Outcome: the Proposed Spec Delta applied to the four spec files
     (verbatim where the delta gives exact text; per its anchors where it
     gives replacement instructions), plus `## Related Plans` backlinks to
     this plan in 05 and 07. No `_Implementation mapping_` or
     reciprocal-link claims yet.
   - Constraint: do not touch unrelated hunks. Record the promotion
     baseline identifier here when applied.
   - Done when: spec text matches the delta; audit results are recorded in
     this plan; `uv run pytest tests/specs -q` passes.
3. **Per-TID writer in BaseTask.**
   - Outcome: `_register_tid_state()` writes to
     `f"{WEFT_TID_MAPPINGS_QUEUE_PREFIX}{self.tid}"`, remembers the message
     ID of its last successful append in an owner-local attribute, and
     best-effort exact-deletes the previous ID after each successful append.
     Payload shape unchanged. Terminal latch behavior unchanged.
   - Files: `weft/core/tasks/base.py`, `weft/_constants.py` (add
     `WEFT_TID_MAPPINGS_QUEUE_PREFIX: Final[str] =
     "weft.state.tid_mappings."`; rename `WEFT_TID_MAPPINGS_QUEUE` to
     `WEFT_TID_MAPPINGS_LEGACY_QUEUE` with a docstring saying it exists
     only for the Task 8 cutover delete — it keeps its value and is not
     removed by this plan), `tests/core/test_task_*` /
     `tests/tasks/test_task_observability.py`.
   - Reuse: the existing `_queue()` cache; the exact-delete surface named
     in §3. No new helper family.
   - Constraints: append first, trim second; a trim failure must not raise
     out of the publication path; no queue read anywhere in the writer.
   - Tests (red-green where practical): snapshot lands in the per-TID
     queue; second edge leaves exactly one row; simulated trim failure
     leaves two rows and the next publish converges to one; terminal
     publish latches once; per-TID queues are excluded from `weft system
     dump` output (prefix exclusion verified, not assumed).
   - Stop if: the writer wants to read the queue, or a dual-write
     (flat + per-TID) mode starts to look necessary.
4. **Shared liveness module + reader migration.**
   - Outcome: `weft/core/liveness.py` (new, small) owning
     `mapping_row_is_live` (moved verbatim from
     `policies/tid_mapping.py`), `list_registry_tids(ctx)` (prefix
     listing → TID set, name-derived), `read_registry_snapshot(ctx, tid)`
     (newest-first bounded peek — SimpleBroker 8's `order="newest"` with
     limit 1 through the peek surface weft already uses — returning the
     greatest-message-ID payload directly; correct under crash residue
     because newest-first ordering is by public message ID), and
     `short_tid_matches(...)` for short→full resolution from names.
     `list_registry_tids` must validate the name suffix (well-formed full
     TID) and ignore malformed names, so a stray
     `weft queue write weft.state.tid_mappings.junk …` cannot inject a
     phantom TID into liveness or admission sets. All flat-queue readers
     migrate to it. Known sites (the authority is
     `grep -rn WEFT_TID_MAPPINGS weft/` at implementation time — this list
     is a starting inventory, not exhaustive): `task_monitor.py`
     (`_active_runtime_tids`, `_destruction_protected_runtime_tids`),
     Manager admission observer, `endpoints.py`, `pruning/runtime.py`,
     `commands/system.py`, `manager_runtime.py::_lookup_manager_pid`
     (kill-pid resolution), `heartbeat.py::_heartbeat_runtime_handle_is_live`,
     `commands/tasks.py` (`_read_tid_state_entries`, `mapping_for_tid`,
     short→full resolution, watch sites), and
     `commands/_spawn_submission.py::_mapping_exists_for_tid` (spawn-loss
     reconciliation — its `since_timestamp` idiom becomes a
     queue-existence/newest-row check). Every migrated site keeps its
     current observable behavior; a site whose behavior would change is a
     stop-and-re-evaluate gate.
   - Read first: each read site's current iteration (grep
     `WEFT_TID_MAPPINGS_QUEUE`); `docs/specifications/07-System_Invariants.md`
     [OBS.11] (liveness evidence must not reanimate terminal state).
   - Constraints: one probe, one module; no per-site reimplementation of
     "newest row"; `core/` must not import `commands/`/`cli/`/`client/`.
   - Tests: destruction-protection parity tests (existing
     `tests/core/monitor/policies/test_tid_mapping.py` cases that cover the
     probe move here); admission tests updated to per-TID fixtures;
     endpoint resolution liveness unchanged in behavior.
   - Stop if: any consumer needs history beyond the newest row — that
     contradicts [OBS.6] as promoted and must surface as a deviation, not a
     workaround.
5. **Retention rework and machinery deletion.**
   - Outcome: TaskMonitor's tid-mapping cleanup is the three-rule policy
     from the promoted Cleanup Boundary text (residue trim; malformed-row
     delete; dead-and-old newest-row delete) — always via exact observed
     message IDs through the existing pruning apply path, never a
     whole-queue delete (an emptied queue vanishes on its own; exact
     deletion cannot clobber a snapshot appended after the monitor's
     read). Deleted: `tid_mapping_candidates`,
     `tid_mapping_streaming_candidates`, `_newest_tid_mapping_ids`, the
     tid-mapping branch of `run_task_monitor_cleanup`, and the
     `exclude_tids=(self.tid,)` self-exclusion (the monitor's own newest
     row probes live via its own PID; if review disagrees, keep the
     exclusion and record why in the Deviation Log).
   - Files: `weft/core/monitor/policies/tid_mapping.py` (shrinks to little
     or nothing — prefer deletion over stubs), `weft/core/monitor/cleanup.py`,
     `weft/core/monitor/task_monitor.py`, `weft/_constants.py` (retire
     superseded policy/waypoint constants), corresponding tests.
   - Constraints: min-age fence keeps its constant
     (`TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS`); selection
     enumerates queues by prefix and reads only each queue's few rows —
     no cross-queue evidence; progress reporting keeps the existing
     `PolicyProgress` shape so PONG diagnostics stay stable.
   - Tests: terminal old entry retired (rows exact-deleted, queue vanishes
     from the namespace); terminal young entry retained; undecidable
     (external-handle) entry retained; live-PID entry retained; malformed
     newest row deleted; residue rows trimmed while newest survives; a row
     appended after selection but before apply survives the apply;
     namespace enumeration is fresh per cycle.
   - Stop if: the new policy starts wanting cross-queue evidence — that is
     the old design leaking back.
6. **LogMonitor extraction and Manager supervision.**
   - Outcome: new `weft/core/monitor/log_monitor.py` service class owning
     task-log scan/collation, MonitorStore, external sink + deferred
     writes, summary disposition, dead-task/reserved destructive slices.
     TaskMonitor retains: liveness reads, registry retention (Task 5),
     self-maintenance vacuum, its PONG surface (slimmed). Manager gains
     `INTERNAL_SERVICE_KEY_LOG_MONITOR` and a spawn payload cloned from the
     task-monitor pattern; both services are persistent function tasks
     under the existing ensure lifecycle.
   - Read first: `weft/core/manager.py` `_build_task_monitor_spawn_payload`
     and service supervision; `docs/specifications/05-Message_Flow_and_State.md`
     Cleanup Boundary owners list (as promoted).
   - Constraints: move code, do not rewrite it — the extraction must be
     reviewable as a move; LogMonitor reads liveness only through
     `weft/core/liveness.py`; no cross-service control-message protocol
     (shared queues + shared module are the whole interface); mode/env
     configuration (`WEFT_TASK_MONITOR_MODE` etc.) keeps meaning for the
     LogMonitor — document the env-var ownership in `_constants.py`
     docstrings.
   - Tests: harness boots manager → both services appear in
     `weft.state.services` with distinct keys; task lifecycle end-to-end
     (run → collation → summary) unchanged; killing LogMonitor leaves
     liveness cadence running and vice versa; PONG payloads for both.
   - Stop if: the extraction wants behavior changes beyond custody, or a
     second supervision path appears.
7. **Liveness cadence — dropped per independent review (finding 8).**
   - A faster fixed cadence prevents no named failure: liveness consumers
     read the namespace on demand through the shared module (nothing is
     cached behind the monitor's cycle), and retention is min-age fenced,
     so a quicker cycle only retires dead entries sooner — cosmetic. The
     split itself (Task 6) already delivers the real decoupling: liveness
     work no longer stalls behind log custody. TaskMonitor keeps its
     existing cycle interval and no new constant or knob is added.
   - Revival condition: a follow-up that adds a consumer of *pushed*
     freshness — e.g. the deferred registered-runner-probe amendment —
     names the staleness failure a faster cadence fixes; add the knob in
     that plan, not this one.
8. **Cutover: legacy delete + grace window.**
   - Outcome: on startup, **LogMonitor** (the destruction custodian —
     keeping delete authority in one service, with no cross-service latch
     signaling) deletes the legacy flat `weft.state.tid_mappings` queue if
     present, and when it deleted one, records an in-process monotonic
     grace deadline (`LEGACY_REGISTRY_CUTOVER_GRACE_SECONDS`, default =
     the tid-mapping min-age). Until the deadline, LogMonitor destructive
     dispositions that would act on *absence* of registry evidence
     (stale_open disposal, dead-task cleanup for non-terminal families)
     are suppressed; positively-proven-dead paths proceed. TaskMonitor
     needs no latch: its retention only acts on registry queues that
     exist, never on absence. Operational note in the plan and CHANGELOG:
     a post-deploy `weft task ping` sweep of non-terminal families closes
     the quiet-task window immediately and doubles as the acceptance
     check (namespace repopulates).
   - Constraints: the latch is one timestamp compared per slice — no new
     state machine, no persisted flag. It exists to prevent one named
     failure (quiet old live task disposed during the evidence gap) and
     nothing else. It arms only on an actual legacy-queue delete, so it
     is inert on every startup after cutover.
   - Named residual risk (accepted; do not "fix" with a persisted flag):
     if LogMonitor crashes and restarts inside the grace window, the
     legacy queue is already gone, the latch does not re-arm, and
     absence-based dispositions resume early. The post-deploy
     `weft task ping` sweep is the mitigation — it closes the window
     within seconds of deploy, making the crash-inside-window overlap
     negligible. Persisting the latch would trade this sliver of risk for
     a durable flag on an ephemeral surface, violating [OBS.18].
   - Tests: with a wiped namespace and a fresh latch, stale_open disposal
     defers; after the deadline (injected `now_ns`), it proceeds; legacy
     queue present → deleted once.
9. **Supersede the bounded-publication plan.**
   - Outcome: `2026-08-25-bounded-tid-mapping-publication-plan.md`
     metadata `Superseded by:` points to this plan; `docs/plans/README.md`
     row updated to match; the inheritance list in §2 is the record of what
     carries over. Run `uv run pytest tests/specs/test_plan_metadata.py -q`.
10. **Traceability reconciliation (final slice).**
    - Outcome: `_Implementation mapping_` claims and reciprocal `Spec:`
      backlinks added together for the new/changed sections ([OBS.6],
      [OBS.6a], [OBS.18], Cleanup Boundary passages, [CC-2.2]); module
      docstrings in `base.py`, `liveness.py`, `log_monitor.py`,
      `task_monitor.py` cite the promoted sections; `00-Quick_Reference`
      cross-refs verified; lessons entry in `docs/lessons.md` if any
      correction during implementation exposed a repeated pattern; rerun
      the repo's traceability/self-check gates and record results.

## 6. Testing Plan

- Harness: `WeftTestHarness` for anything involving manager, services, or
  task lifecycle; `broker_env` / real `Queue` for queue semantics. Do not
  mock queues, broker, process lifecycle, or the liveness probe — mocks are
  acceptable only for the external JSONL sink filesystem boundary and for
  injected `now_ns` (both existing patterns).
- Time: all age/grace assertions use injected `now_ns` (existing monitor
  test pattern), never sleeps.
- The one end-to-end proof that matters most (add it early, keep it real):
  run a command task under the harness → assert its
  `weft.state.tid_mappings.<tid>` queue exists with one row → task
  completes → newest row has `terminal: true` → advance `now_ns` past
  min-age → monitor cycle exact-deletes the rows and the emptied queue
  vanishes → namespace listing no longer contains the TID. This single
  test protects [OBS.6], [OBS.6a], the retention rules, and the
  namespace-as-index contract.
- Wipe-rebuild proof for [OBS.18]: mid-run, delete a live task's registry
  queue; assert the task's next edge republishes the snapshot. (The grace
  window is a *startup* latch armed by the legacy delete — the mid-run-wipe
  case is protected by min-age/stale-open fences plus republication, so the
  grace assertion belongs to the Task 8 startup test, not here.)
- Edge case that is tempting to skip and is in scope: trim-failure residue
  (two rows) read path — readers must take the greatest message ID, and the
  monitor must trim without touching the newest row.
- Out of scope for testing: multi-version interop (single current
  contract — no mixed old/new reader tests).

## 7. Verification and Gates

Per-task: the test files named in each task, run narrowly
(`./.venv/bin/python -m pytest tests/core/monitor -q` etc.).

Final gates (all must pass from the repo env, per CLAUDE.md §5):

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs -q
```

Post-deploy observation: `weft queue list` shows the namespace populating;
`weft status` remains correct through cutover; after one grace window plus
one liveness cycle, no legacy queue and no orphaned registry entries for
terminal tasks older than min-age.

## 8. Independent Review Loop

- Reviewer: an agent from a different family than the author when
  available; otherwise an independent fresh-context session.
- Reviewer reads: this plan in full (Design Compass first), the Proposed
  Spec Delta, `docs/specifications/07-System_Invariants.md` [OBS.6]–[OBS.6a]
  and [OBS.13.7] as currently committed, `weft/core/monitor/policies/tid_mapping.py`,
  and `weft/core/tasks/base.py` publication call sites.
- Prompt: the writing-plans.md recommended prompt, plus: "Weigh every
  finding against the plan's Design Compass: does it improve correctness,
  or does it reify process? Flag both directions — missing correctness
  protections AND removable ceremony. The simplest consistent Unix-like
  shape wins ties."
- Author must respond to each finding explicitly (adopt, rebut, or record
  out-of-scope) before the spec-promotion slice.

## 9. Out of Scope

- Payload changes: `activity`/`waiting_on` eviction, `started` → `observed`
  rename, monitor-diagnostics relocation. Severable follow-up.
- Registered runner-plugin death probes (amending the "no registered-probe
  destruction rule"). Named follow-up decision; this plan keeps the
  payload-only probe.
- Any SimpleBroker change, including `peek_last`.
- A death-observations/verdict queue or any cross-service protocol.
- `weft.log.tasks` behavior changes of any kind.
- Renaming TaskMonitor, its service key, or `WEFT_TASK_MONITOR_*` env vars.
- Pruning-command (`weft system prune`) UX changes beyond pointing the
  tid-mappings family at the new namespace.

## 10. Fresh-Eyes Review

Author self-review: completed 2026-08-27, separate pass after drafting.
Findings, all fixed in place:

1. Task 3 originally deleted `WEFT_TID_MAPPINGS_QUEUE` after Task 5, but
   Task 8's cutover delete still needs the legacy name — resolved by
   renaming to `WEFT_TID_MAPPINGS_LEGACY_QUEUE`, retained.
2. Task 8 originally had TaskMonitor delete the legacy queue while
   LogMonitor honored the grace latch — a cross-service signal this plan
   forbids. Resolved: LogMonitor (destruction custodian) owns both the
   legacy delete and its own in-process latch; TaskMonitor never acts on
   absence, so it needs no latch.
3. The 05 Cleanup Boundary owners delta anchor was ambiguous ("amend the
   monitor bullet" — several bullets mention the monitor). Resolved with
   exact insert/replace anchors and a bounded grep sweep instruction.
4. The wipe-rebuild test wrongly asserted grace suppression for a mid-run
   wipe, but the latch arms only at legacy-delete startup. Resolved: the
   [OBS.18] test proves republication; the grace assertion moved to the
   Task 8 startup scenario.

Independent review: completed 2026-08-27 (fresh-context reviewer, stance
per §8). Dispositions below.

## Independent Review Record

Append-only. Each finding was weighed per the Design Compass: does the
change improve correctness, or reify process?

| # | Severity | Finding | Disposition |
|---|----------|---------|-------------|
| 1 | blocking | Delta anchored to uncommitted worktree text ([OBS.6a] absent at `33e1ab7`); Task 5 deletes uncommitted code | **Adopted.** [OBS.6a] delta rewritten as full self-contained replacement/insertion; Spec Baseline coordination note hardened into a Task 2 precondition with both resolution branches named. |
| 2 | blocking | Promised [OBS.13.7] delta text missing | **Adopted.** Targeted replacement passage drafted from the committed text at `33e1ab7`, including the row→queue evidence model and the policy-file pointer move. |
| 3 | should-fix | Reader-migration list materially incomplete (kill-pid, heartbeat, `commands/tasks.py`, spawn reconciliation) | **Adopted.** Task 4 inventory expanded; grep declared the authority; behavior-change at any site is a stop gate. |
| 4 | should-fix | Malformed-payload deletion dropped; phantom-TID injection via junk queue names | **Adopted.** Rule 2 (malformed delete) added to the Cleanup Boundary delta; name-suffix validation required in `list_registry_tids`. |
| 5 | should-fix | Whole-queue delete races a concurrent append | **Adopted** (correctness at zero added concept): retention deletes exact observed message IDs; the emptied virtual queue vanishes on its own. Delta and Task 5 updated; race test added. |
| 6 | should-fix | [OBS.18] universalizes over unaudited surfaces | **Adopted.** Promotion gate added: recorded audit of the four other `weft.state.*` surfaces in Task 2; scope the invariant if any fails. |
| 7 | consider | Grace-latch restart hole unnamed | **Adopted** as documentation only: residual risk named in Task 8 with the ping sweep as mitigation and an explicit instruction not to persist the latch. |
| 8 | removable-ceremony | Faster liveness cadence prevents no named failure | **Adopted.** Task 7 dropped by the plan's own compass; existing cadence kept; revival condition recorded (a future pushed-freshness consumer, e.g. the runner-probe follow-up). This reverses an earlier design intention; flagged to the plan owner. |
| 9 | consider | Retention passage structurally nested under LogMonitor bullet | **Adopted.** Delta instruction now promotes the passage to its own owners-list bullet. |

Reviewer's direct answers at review time: design implementable; delta not
yet promotable pending findings 1, 2, 4, 6 — all four resolved above.
Clean-pass items the reviewer verified are recorded in the review
transcript summary: publication call sites, append-before-trim ordering
(a live registry queue never empties — load-bearing because virtual queues
vanish when empty), residue-trim race freedom, self-exclusion removal
soundness, name-length and dump-exclusion checks, benchmark-gate
sequencing, migration-free rollback, and structural runbook compliance.

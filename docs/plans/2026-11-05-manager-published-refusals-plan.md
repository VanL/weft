# Manager Published Dispatch Refusals

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1], [CLI-1.3]; docs/specifications/07-System_Invariants.md [OBS.6], [OBS.6a], MANAGER.11
Superseded by: none

Class: 5, risky: adds an operational read model at Manager dispatch/custody and
public status boundaries. Hardening applies. The owner has now authorized implementation per this plan.
New recovery commands remain out of scope.

## 1. Goal

Publish why a Manager refuses accepted work beyond one maintenance pass in its
existing complete task-state snapshot. Show the reason, actual scope, causal
records or measurements and ending condition through ordinary status. Capture
existing dispatch decisions; do not create another scheduler or report every
cleanup obligation as blocked dispatch. A lane refusal is one aggregate, not
one copied entry per queued task.

## 2. Governing sources and baseline

Read `docs/program-theory.md` at `963dc83e` ([REV-WT-006], [WT-2a.2],
[WT-2a.6], [WT-2a.7], [WT-2a.13], [WT-2a.15], [WT-4], [WT-7]); the
source spec sections in the metadata; `AGENTS.md`; the agent-context README,
decision hierarchy, principles, engineering principles, lessons pointer and
`docs/lessons.md`; and the writing-plans, hardening-plans, testing-patterns and
review-loops runbooks. Use `skills/test-audit/SKILL.md` for test design and
`skills/call-agent/SKILL.md` for cross-family review.

Related work: the completed Manager wake/idle/settlement repair is context;
`docs/plans/2026-11-05-scoped-ambiguous-launch-dispatch-plan.md` owns D1's
scheduling partition. Rebase this exact delta on D1's final source before
implementation. D5 must not restore a whole-context ambiguous-launch gate.

Spec baseline: `963dc83e`, with the D1 operative delta as an explicit dependency.
Promotion strategy B: code and all spec blocks land atomically. Promotion
baseline identifier: `963dc83e` plus completed D1 and this in-flight implementation
worktree delta; all four exact Replacement blocks are now applied. Record intermediate slice baselines in the
execution log, not this field; fill it only when all Replacement blocks land.

## 3. Placement and source facts

The carrier is `dispatch_refusals` inside the full
`weft.state.tasks.<manager_tid>` snapshot. `Manager._build_tid_state_payload`
already adds `scheduler_frontiers`; `BaseTask._register_tid_state` appends the
complete body and returns success/failure without queue read/dedup/pruning.
The latter is already called before Manager bootstrap. A successor has a new
TID and cannot overwrite a predecessor's queue. `manager_launch_custody_pending`
retains predecessor checkpoints while reservations remain. Never append a
refusal-only body as the newest task mapping.

`_publish_scheduler_checkpoint` also changes dirty/undurable flags. A diagnostic
append uses the complete publisher without changing those flags or completing
bootstrap. Its successful write is not permission to bypass the checkpoint
owner's normal success transition. Prove this across a second Manager crash.

Project `system_status` already captures latest task-state rows once. Enrich
Manager snapshots from that same captured map. Manager list/status use
`latest_task_state_rows(..., tids=...)` for only the selected Manager TIDs.
Extend `_manager_snapshot`, `ManagerSnapshot`, `_manager_record_to_json`,
`_manager_json_record`, `_render_status`, `manager_list_command` and
`manager_status_command` through one shared observation conversion.
`dispatch_eligible` keeps its existing leadership/liveness meaning.

Alternatives: registry metadata was rejected because per-request scheduler
state does not belong in discovery records. A dedicated task-owned queue is a
valid primitive, but needs discovery, current-fact replacement and crash cleanup
that the existing full task snapshot already supplies. `ctrl_out` mixes terminal
proof/stream retention with a different lifecycle. Titles are not durable facts.
No blanket ban on new queues is inferred from [ALT-WT-010]/[ALT-WT-019].

## 4. Shape, ownership and cost

`dispatch_refusals = {assessment, items}`. `assessment` is `known` or
`unavailable`. Initial or unreadable diagnostics are unavailable, not known
empty. An ordinary failed per-task reconciliation/reap read changes no Manager
mode, checkpoint or diagnostic under [MA-1.8]. Reader failure to acquire the
Manager's own snapshot is separately unavailable to that reader.

Each item has `reason`, `scope`, `lane`, `barrier` and `ends_when`. The last two
are immutable descriptors derived from reason by one shared table, not separately
maintained state. Scope selects one of two payload shapes:

- `dispatch_lane`: `queue`, `known_pending_count`, `count_basis`,
  `oldest_request`, and `measurements`. There is one item per lane/barrier,
  never a `subjects` array of every queued request. `oldest_request` is the
  oldest pending source row's nullable broker message ID in that refused lane,
  not a claim about the oldest
  eligible family-scheduler candidate. Lane scope states the barrier applies
  to the lane, even if its head also has an independent ancestry hold.
- `request` or `family_path`: exact causal `subjects` and queue/message
  `evidence`. For a path, name the held ancestor(s), not all queued descendants;
  immutable ancestry defines the closure. References name actual retained
  obligations, not copied TaskSpecs, bodies or host-process arrays.

All broker IDs are lossless ints internally and [SB-0.2] strings in external
JSON; TIDs stay strings. A causal message ID may be null when a failed read or
uncommitted write leaves it unknown. Preserve known IDs; null never means absent.
`measurements` reuses the existing used/reserve/lane-limit observation, or null.

### Lane counts and witnesses without a backlog scan

`Queue.stats()` is not a cheap maintained counter: SimpleBroker SQLite's
`GET_QUEUE_STAT` uses SUM/COUNT over queue rows. Do not call it, enumerate the
source, or serialize all pending IDs to build diagnostics.

Use the current Manager index cardinalities: `len(FamilyScheduler._pending)`
plus `len(_held_pending)` for the public lane, with a small read-only
`indexed_pending_count` accessor if needed by production diagnostics. This is
an owner-indexed observation, not the total shared-queue backlog. Do not count
`_family_invalid` as schedulable indexed work. `count_basis` is always
`manager_indexed`; null means not assessed (including internal/bootstrap cases
without a suitable existing index). Never substitute zero for an unknown count.
The count may lag concurrent source writes and other Managers. Render its basis
and last-published snapshot identity; it is not an exact current queue count.

At most one `peek_one(with_timestamps=True, include_claimed=False)` per refused
lane per due maintenance pass supplies the oldest pending source witness;
reuse the source read if the owner already made it. Use the task-owned connected
queue, not a fresh connection. A failed read preserves the last witness as
last-observed and cannot invent work. No source row means no lane refusal of
accepted source work. Source counts/head observations are refreshed only at the
existing pass boundary, never whenever a normal checkpoint serializes the field.

Lane continuity is keyed by `(barrier kind, lane, stable causal identity)`,
not reason code, head ID, backlog count, usage sample or retry timestamp.
Changing from unavailable usage to exhausted capacity changes the reported
reason but does not restart standing age while the same measured-resource
barrier continuously refuses work. An actual resolved/open barrier ends it. Head changes do not
restart an enduring lane barrier; a head alone does not keep a resolved barrier
alive. Request/path continuity uses the exact retained causal obligation.
Counts/witnesses carry no dispatch authority and do not trigger publication by
themselves. Other ordinary owner snapshots may carry their latest observation.

### Dispatch reason inventory

| Reason | Actual dispatch owner/scope | Ends when |
|---|---|---|
| bootstrap_incomplete | `_refresh_scheduler_evidence` / `_bootstrap_scheduler_evidence`; public lane with accepted work | bootstrap_complete: inherited evidence read and own first checkpoint commit |
| checkpoint_uncommitted | `_publish_scheduler_checkpoint` and known post-launch responsibility; actual public-lane durability fence | checkpoint_committed: required checkpoint publication succeeds |
| launch_receipt_missing | `_hold_ambiguous_launch`, `FamilyScheduler.hold`, `_held_launch_feedback`; held request/ancestry closure | matching_started_evidence: D1 accepts post-launch process evidence for eligibility |
| admission_usage_unavailable | `_admission_allows_queue` / `_record_admission_decision`; actual refused lane | fresh_usage_available: a fresh observation is available; exhaustion changes reason rather than falsely clearing |
| measured_capacity_exhausted | same owners; actual refused lane | fresh_capacity_permits: fresh capacity permits that lane |
| startup_feedback_pending | active launch/ordinary startup window blocks the public lane while more accepted work waits | startup_window_available: normal membership/feedback releases sufficient window |
| classification_unavailable | `_classify_family_requests` failed on witnessed accepted unclassified rows; only those requests/path | classification_succeeded: existing index/classification repair succeeds |

For D1, any valid matching post-launch process evidence can release scheduling
eligibility even if creation time is unknown. That does not ACK custody or prove
death. `_snapshot_hands_off_launch` still needs exact matching scoped process
identity for custody handoff; `_scheduler_evidence_live` preserves death rules.
Do not use the stronger custody condition as D5's eligibility-ending condition.

`terminal_route_unreadable`, `terminal_publication_failed` and
`identity_evidence_rejected` are not D5 dispatch reasons: in the current source
they retain cleanup/custody while unrelated dispatch proceeds. The child is
already launched, or the evidence was deliberately skipped. Keep those existing
warnings/observations; do not label them a queued dispatch refusal unless a
future reviewed source change actually gates accepted work on them.

Empty input, one-launch-per-turn allowance, transient proof grace, explicit
STOP/drain and noncanonical ownership use existing task/service state rather
than invented dispatch refusals. Failed empty-source repair is not a refusal;
already-indexed and internal work remain unaffected by classification retry.
No new catch-all reason or queue scan may fill an unverified inventory gap.

### Standing passes, clear/set churn and emission

A due `_refresh_scheduler_evidence` attempt after its early due check advances
one owner-local maintenance generation, including a bootstrap failure that
establishes an existing barrier. Calls returning before the due check do not.
Use an equivalent existing generation if present; otherwise add one local
counter, not a clock or timer. A new refusal candidate becomes standing only
when accepted work and the same causal refusal persist into a later generation.
Normal startup resolved in one generation forces no diagnostic append.

Capture branch-owner changes, but assess/flush after `_process_manager_reactor_turn`
returns normally to its outer `_process_reactor_turn` wrapper. This covers early
returns after all owner work; no write from the earlier registration phase.
A retry may clear admission flags then re-deny capacity within one pass. The
final observation, not those intermediate mutations, determines continuity.
A saturated lane with unchanged reason/barrier therefore remains standing and
forces no clear/set append pair each second.

Emit at most one diagnostic-forced full snapshot per due maintenance generation,
only for semantic barrier establishment/resolution/reason change or changed
request/path causes. Count, lane-head, measurement and retry-clock churn alone do not dirty
publication. No known-clear append on each construction/normal launch is needed;
normal complete owner reports carry the current unavailable/known assessment.
If a same-generation normal full snapshot already carried the required
observation, do not force another append. The emission site tracks successful
semantic observation locally, without reading the queue to deduplicate [OBS.6a].
Failed appends do not advance that marker. Retry only on an existing due pass;
no zero-timeout retry, periodic diagnostic report, heartbeat or new waiter.
A quiet held-work recheck remains the existing owner's absence/custody work.
A process can die before publishing; no broker-write availability guarantee is
invented. Status describes last published data and names its source snapshot.

### Invariants and difference register

Preserve no replay, positive settlement, full checkpoint shape, task-owned proof
precedence, status read-only behavior, existing single-deleter cleanup and the
CLI/client capability boundary. Diagnostics never clear a checkpoint gate,
complete bootstrap, supply custody proof, alter liveness or create capacity.
A successor re-derives current refusals; it never adopts a dead owner's
observations as execution authority. Historical owners stay visibly historical.

Cost: refusal payload is O(H + L), H being actual causal held-request/path
obligations and L the fixed lane/barrier inventory, not O(N) queued source
requests. A normal full snapshot still has its existing O(live-resident) cost;
D5 adds no queued-body scan or source-cardinality term. A pass makes at most
one source-witness read per refused lane; normal snapshot building makes zero.
Do not claim O(1) whole snapshot writes or constant total CPU.

| Surface | Intended change | Must stay unchanged |
|---|---|---|
| active Manager status | show scoped refusal and ending evidence | leadership/liveness and dispatch_eligible meaning |
| queued lane backlog | aggregate count basis/head, not N task copies | accepted durable source and scheduler indexes |
| sustained capacity retry | stable final observation, no clear/set diagnostic pair | actual capacity rechecks and existing timers |
| own snapshot | add diagnostic to complete append | custody, bootstrap, OBS.6a and cleanup ownership |
| unavailable read/write | honest last-observed/unavailable result | no new Manager mode or launch permission |

## 5. Exact proposed spec delta

### [MA-1.8] — insert after `**Admission control [MA-1.8]**`

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
A standing dispatch refusal is reported in dispatch_refusals on the Manager's complete weft.state.tasks.<manager_tid> snapshot. It describes actual accepted work refused beyond one due maintenance pass, not every retained cleanup obligation. The observation names the existing reason, actual request/ancestry-path or dispatch-lane scope, causal retained records or resource measurements and ending condition; it never grants dispatch, handoff, replay or settlement. A lane refusal is a fixed-size lane/barrier aggregate with an owner-indexed pending-count observation and oldest pending source witness, never an enumeration of queued subjects. A normal per-task read failure changes no Manager mode, checkpoint or diagnostic. Diagnostic publication uses the complete task snapshot without completing bootstrap, clearing undurable responsibility or substituting for the checkpoint owner's success transition. A successor derives observations from retained evidence, preserving predecessor custody. D1's post-launch started evidence may end an eligibility hold without satisfying scoped custody handoff or death proof.
```

### [MF-5] — insert after `### 5. State Observation Flow [MF-5]`

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
Manager snapshots carry dispatch_refusals: {assessment, items}; assessment is known or unavailable. Items contain reason, scope, lane, barrier and ends_when; barrier/ends_when are descriptors derived from reason. dispatch_lane items contain queue, known_pending_count, count_basis=manager_indexed, oldest_request and measurements, not all queued subject IDs. The count is nullable and describes the Manager's indexed observation, not a complete current shared-source count. The oldest_request is the oldest pending source witness in that refused lane, not an oldest-eligible scheduling claim. Request/family_path items contain exact causal subjects and queue/message evidence; family_path names held ancestors, not every descendant. Unknown causal message identities may be null after failed reads/writes without implying absence. The reason inventory is bootstrap_incomplete, checkpoint_uncommitted, launch_receipt_missing, admission_usage_unavailable, measured_capacity_exhausted, startup_feedback_pending and classification_unavailable. ends_when is respectively bootstrap_complete, checkpoint_committed, matching_started_evidence, fresh_usage_available, fresh_capacity_permits, startup_window_available or classification_succeeded. Matching started evidence can release D1 eligibility despite unknown process creation time; scoped handoff and positive-death rules remain stricter. Normal snapshots blindly append complete state under OBS.6a. Diagnostic construction never enumerates the source or calls whole-queue counts. At most one source-witness read per refused lane occurs in an existing due pass, never in normal snapshot serialization. Lane continuity is keyed by barrier kind, lane and stable causal identity, not reason code or head/count/usage/retry-clock churn; a changing reason within the same continuously closed barrier does not restart standing age; transient retry clearing followed by denial in one pass is not a resolution. At most one diagnostic-forced append per due generation records semantic refusal changes; stable saturated lanes add no diagnostic clear/set reports or timers. Failed append markers advance only on success, and retries use existing maintenance opportunities.
```

### [CLI-1.2.1] — insert after `### `status` - Show project status [CLI-1.2.1]`

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
Ordinary status exposes dispatch_refusals from the matching Manager's complete task-state snapshot. Project status reuses its captured task-state map; Manager list/status read only selected Manager TIDs. The shared Python ManagerSnapshot uses the same observation and source queue/message identity. Human output shows reason, scope, lane, ending condition, count basis and observed lane head, plus up to three causal subject/evidence references per request/path reason with total causal counts and an explicit --json continuation. JSON retains all actual held-cause references but does not expand a lane aggregate into queued-task IDs. Known broker message identities use lossless SB-0.2 strings externally and ints in Python; TIDs stay strings. Count/head data are last-observed, not a current global backlog guarantee. Missing/malformed/unreadable Manager observations are unavailable, not a known empty set or permission to dispatch. Historical owners are never merged into the canonical live owner's refusal set. No active probe, global task-log replay or Monitor-private lookup is added.
```

### [CLI-1.3] — insert after `## Control Commands [CLI-1.3]`

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
Manager list/status additionally expose the shared dispatch_refusals observation from the matching Manager-owned task-state snapshot under CLI-1.2.1. It is not part of the service-owner registry schema and does not change dispatch_eligible, canonical selection or bootstrap readiness. This observation adds no retry, delete or custody-release verb.
```

All four blocks were promoted with implementation; adjacent mappings and
backlinks are synchronized. The amendment-only result below is historical.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## 6. Implementation tasks and hardening

1. Rebase on final D1, verify the source owners above, and write RED cases before
   code. Record answers: why do unknown creation times release eligibility but
   not custody; why is Queue.stats not a cheap diagnostic counter; and why does
   an ignored identity mismatch not prove a blocked request? Stop if a cleanup
   obligation is being turned into a new execution gate.
2. Capture existing refusal owners and bounded observations. Introduce one pure
   reason-descriptor table, a local generation/continuity record and a complete
   snapshot projection. Keep branch decisions unchanged. Flush at the outer
   normal turn-return boundary, coalescing with normal publications. Stop for a
   new timer, second scheduler, queued-subject copy or snapshot-builder read.
3. Wire one validated public observation through `weft/commands/types.py`,
   `weft/commands/manager.py`, `weft/commands/system.py`, `weft/cli/app.py`.
   Manager owners stay in `weft/core/manager.py`; `weft/core/family_scheduling.py`
   may expose the production-used O(1) indexed-count accessor. Treat
   `weft/core/tasks/base.py` and `weft/core/task_state.py` as governing full-
   snapshot constraints, not alternate publishers. Apply all four spec blocks
   atomically with code, mappings, README and CHANGELOG; then mark promoted.
4. Run fault/restart/cost acceptance and completed-work cross-family review.
   Re-run WT-7 on the final implementation boundary, update the theory audit,
   record findings/dispositions and mark plan/index completed only after work
   is actually done. The owner's subsequent implementation request supplied authorization; plan
   status alone did not.

Queue/format/CLI changes stay additive and current-contract only. Revert code
and spec delta to roll back diagnostics; never delete reservations/checkpoints.
The only writer is the Manager task; existing LivenessMonitor owns task-state
cleanup. A diagnostic-only or incomplete checkpoint row is a stop condition.
Test a successor failing bootstrap, publishing its complete diagnostic state,
then dying: another successor must preserve every predecessor reservation.
No operator settlement or irreversible data action is part of D5.

## 7. Firing tests and acceptance

Keep Manager, broker, actual turns, reservation/checkpoint and OS process
ownership real. Fault only the actual usage/read/write boundary. Extend
`tests/core/test_manager.py`, `tests/core/test_admission_recovery.py`,
`tests/core/test_manager_family_maintenance.py`, `tests/commands/test_status.py`,
`tests/commands/test_manager_commands.py` and `tests/cli/test_status.py`.

- Every reason gets an owner-boundary transition and intended failing control.
  A failed empty-source classification read and cleanup-only terminal/identity
  holds do not fabricate refusal items. An indexed/internal candidate still
  proceeds when another unclassified request's indexing fails.
- D1 matching post-launch evidence with creation_time=null clears eligibility
  and its receipt-missing item, but preserves its exact reservation and scoped
  custody requirements. No automatic replay follows from missing evidence.
- At two backlog sizes N and 2N, keep live residents fixed and drive normal
  checkpoint publication through a complete drain. Refusal payload per lane
  remains fixed-shape; diagnostic builder performs zero queue-body enumeration,
  Queue.stats or whole-queue count operations. Total added serialization is
  linear in checkpoint count, not quadratic in queued-task count. Count reports
  its owner-indexed basis; unseen concurrent submissions do not masquerade as
  fully counted work. Verify one oldest-witness read per lane/pass at most.
- Saturate a lane across at least three actual due maintenance generations.
  Exercise the real retry clear/re-deny branches. The same barrier persists
  when heads/counts/usage change; diagnostic-forced append counts are [1,0,0]
  after establishment, not clear/set pairs. Alternate unavailable usage and
  exhausted capacity while the same resource barrier stays closed: standing
  age establishes once, current reason changes remain observable and N due
  generations force at most N diagnostic appends. Resolve it and observe one clear.
  Count actual owner writes/turns; retain legitimate capacity/evidence timers.
  For N genuinely flapping generations, forced appends are at most N, and
  incidental turns add zero; normal checkpoint writes are counted separately.
- Normal SQLite startup resolves before a second due generation and forces
  zero diagnostic appends. An enduring startup hold with waiting accepted work
  becomes standing and publishes once. Removing delayed-standing logic must
  fail the normal-startup control.
- Failed per-task reconciliation read changes no diagnostic/checkpoint/mode;
  a failed status read is unavailable to that reader. Failed diagnostic append
  retains the previous full snapshot and retries only on an existing pass.
  A second-crash bootstrap test proves predecessor custody survives. Replacing
  full publication with a refusal-only row must fail that proof.
- Status uses existing state acquisition with empty raw history and unavailable
  Monitor store. Superseded owner observations remain historical. Public IDs
  above 2**53 remain exact. Four causal references yield three text samples,
  a total and --json continuation, with all four in JSON; lane aggregates do
  not claim to enumerate every affected queued task.

Plan-only gates after edits: load `. ./.envrc`, run `tests/specs`,
`bin/check-doc-paths`, `bin/check-dom15-fixtures`, `bin/coalesce-check` and
`git diff --check`. These establish no runtime acceptance.

Future implementation gates, at normal configured concurrency:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests
./.venv/bin/python -m pytest -m "" tests
./bin/pytest-pg --all tests
./.venv/bin/python -m pytest integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
bin/check-doc-paths
bin/check-dom15-fixtures
bin/coalesce-check
git diff --check
```

Record exact counts, skips, platform/provider limits and raw failures. Do not
reduce contention, serialize acceptance or loosen timeouts to hide a failure.
Traceability covers all current code/test/consumer roots and introduced debt.

## 8. Recovery boundaries and out of scope

Owner: "I don't think that we transparently replay an ambiguous launch. That
is too much magic." Positive current-protocol pre-launch proof may permit the
existing exact move back to the source. Ambiguous launches stay held.
No task retry/delete commands exist in the inspected CLI. Proposed terminal
retry would be a new ordinary attempt/new TID with provenance, not reanimation;
unknown-original retry needs an explicit duplicate-risk owner decision. Proposed
task deletion must not implicitly settle unknown custody. Exact IO/provenance
and deletion scope need their own recovery plan. D5 names real ending evidence
and the absence of a supported manual release rather than inventing one.

Out of scope: D1 scheduling changes, cleanup-only custody diagnostics, new
recovery verbs, force replay/settlement, dedicated queue lifecycle, a second
control plane, Monitor-private reads, lossless refusal history, a hard total
snapshot byte cap, and extending the existing task-event `status --watch`.

## 9. Review, updated theory test and audit

Author fresh-eyes amendments: lane aggregates replace queued subject copies;
source stats were verified to be SQL counts and are not used as cheap counters;
cleanup-only holds were removed; D1 eligibility and custody evidence were split;
continuous lane barriers suppress per-pass clear/set churn. The final source
review and tests must challenge these precise claims, not just JSON builders.

The two-factor fit test uses theory `963dc83e` and the fixed exact normative
diff. Before review the author writes a compact five-part account outside this
plan. A fresh different-family agent gets only theory+diff from a scratch cwd,
not this plan, logs or author account, and writes its own compact five-part
account. Preserve both verbatim here after comparison. Classify divergence as
change deficiency, document deficiency or agent reasoning (contested); change
the theory only for a document deficiency. The test informs the owner and does
not gate closure or require an owner key. Repeat on actual final code delta.

Superseded-trial ledger (not approval of this amended boundary): the original
registry proposal fit with strain and was replaced by full task-state snapshots.
The later isolated fit found omitted human causal references; that change
deficiency was accepted and independently verified. Permanent-hold recovery,
physical broker-write failure and overbroad claims that all new queues are
forbidden remain explicitly bounded, not hidden by declaring a universal fit.
Prior isolated boundary hash: `7860d06e6c20986b1a5a2f907a9388b6bc973a8c82f4dab809592d34dbfc831f`;
verified presentation-repair hash: `0866bff0858cef0bda49c27ff03301cf9359b31f3bea7bcd67a6335fb85b869f`.
Those trials do not establish fit of this amended boundary.
The previous unabridged trial transcripts are superseded review history, not
operative implementation instructions. Fresh compact accounts below preserve
the required WT-7 evidence without repeating hundreds of narrative lines.

| Theory claim | Planning disposition | Required runtime evidence |
|---|---|---|
| WT-2a.15 scoped visible refusal | drifted until implementation; D1 owns scope, D5 observation | ordinary status during actual standing denial; correct ending evidence |
| WT-2a.2 owner facts | predicted aligned, unmeasured | diagnostics never authorize dispatch/custody; status reads only public queues |
| WT-2a.6/4.3 quiet and cost | predicted aligned, unmeasured | actual generation/write counts, no new timers, no O(N) queued copies |
| WT-2a.7/13 custody | predicted aligned, unmeasured | failure/second-crash cases preserve reservations and no duplicate execution |

### Amendment review log

- User's B-grade review: accepted queued-lane serialization cost, non-dispatch
  reason leakage, D1 ending-evidence mismatch, retry clear/set write churn and
  excessive review-log bulk. This operative rewrite addresses all five;
  independent review and compact blind fit comparison are recorded below.

### Compact two-factor fit evidence

Fixed theory: `963dc83e`; normative boundary SHA-256: `d2ff66ec4c52c115ae91d2a38915b0dede9f051b61d4ea23ffdde35a44b55485`.
Author account was fixed first. A fresh Google-family agent received only theory
and the exact diff from a scratch directory, excluding plan/log/author context.
Both accounts contain all five WT-7 parts; both are preserved verbatim.

Author (185 words):

```text
Verdict: fits, with a concrete cost boundary.
Sections: WT-2a.15 requires scoped visible denial; WT-2a.2/6 assign complete facts to the Task owner and one reactor; WT-2a.7/13 preserve custody and stronger death/handoff proofs; WT-4 requires measured cost and few concepts.
Refusals: ALT-WT-020 excludes Monitor-private status; ALT-WT-009 excludes log reconstruction; ALT-WT-002 excludes diagnostic retry timers. A dedicated queue is valid but adds an unnecessary lifecycle here. No diagnostic may become a launch permit.
Falsifiable predictions: a lane with N queued tasks adds no N-subject array or queue enumeration to a checkpoint; a continuously closed resource barrier keeps standing age despite reason changes and emits at most one diagnostic per due pass; any valid matching started evidence ends D1 eligibility despite unknown creation time, while custody stays held; cleanup-only obligations create no dispatch-refusal item; failed publication preserves complete predecessor custody.
Worst-fitting sentence: WT-4.4 “As few concepts as possible.” Seven reason descriptors and local generation/continuity bookkeeping are additional concepts. They fit only as a projection of existing decisions, with count basis and staleness explicit, not a second scheduler or pretend exact backlog count. Runtime write/read scaling must verify that claim.
```

Independent account (206 words):

```text
**Verdict:** Fits with strain.

**Sections:** `[WT-2]` items 2–3, `[WT-2a.1]`, `[WT-2a.2]`, `[WT-2a.13]`, `[WT-2a.15]`, `[WT-3]` (Manager, Runtime-state queue, Evidence), `[WT-4.1]` (Queues carry facts), `[WT-4.3]` (One reactor; Availability and death), `[WT-4.4]` (The client mirrors the CLI; Observable with standard tools), `[WT-7]`, `[REV-WT-005]`, and `[REV-WT-006]`.

**Refusals:** Refuses O(N) whole-queue source enumeration or task-graph scans (`[ALT-WT-016]`), unconsumed relational/event tables (`[ALT-WT-019]`), reading `MonitorStore` from status paths (`[ALT-WT-020]`), global `weft.log.tasks` scanning (`[ALT-WT-009]`), defensive polling timers (`[ALT-WT-002]`), a separate index or cache subsystem (`[ALT-WT-010]`), and lossy IDs (`[ALT-WT-022]`).

**Falsifiable prediction:** Under a saturated lane holding $N$ queued tasks or rapid reason churn within one continuously closed barrier, a due maintenance pass performs $\le 1$ source-witness read and $\le 1$ diagnostic snapshot append ($O(1)$ broker operations, zero whole-queue scans or new timers), while `weft status` renders the standing refusal, causal records, and ending condition from `weft.state.tasks.<manager_tid>` alone even with `TaskMonitor` disabled.

**Worst-fitting sentence and tension:** *"D1's post-launch started evidence may end an eligibility hold without satisfying scoped custody handoff or death proof"* (and *"Matching started evidence can release D1 eligibility despite unknown process creation time"*). This strains `[WT-2]` item 3 and `[WT-3]` (Evidence), which ground process identity and custody handoff in scoped `(pid, create_time)`, creating a split between dispatch-eligibility release and strict custody/liveness proof (`[WT-2a.13]`).
```

Comparison: author says fits with a cost boundary; independent says fits with
strain at D1's eligibility/custody split. This is an accepted existing boundary
tension, not a D5 change deficiency: positive started evidence can reopen
eligibility while exact scoped proof still governs ACK and death. Any inference
that D5 relaxes those stronger custody rules is contested agent reasoning, not
resolved by changing the theory. Cost/no-extra-timer predictions agree and
remain runtime obligations, not claims proven by a plan. No theory edit follows.

### Amendment plan review

- Different-family scoped review verified all five technical revisions against
  supplied source excerpts: lane aggregates/count basis, seven real dispatch
  reasons, D1 eligibility ending evidence, generation/write bounds and shorter
  plan. It suggested nullable initial source witnesses; the operative shape
  explicitly makes oldest_request nullable and preserves prior observations.
- Its missing-account finding was accepted as documentation work and is filled
  above. Its claim that the fit test itself blocks closure is rejected: WT-7
  explicitly makes the comparison informative, not an owner-key/closure gate.
- Follow-up after that review: barrier continuity no longer includes reason
  code, so alternating unavailable/exhausted observations within one closed
  resource barrier cannot postpone standing forever. The test now covers
  that transition and the at-most-N append bound. Scoped verification passed
  after the compact accounts were added; see the final result below.

## Revision Log

| Date | Baseline | Change |
|---|---|---|
| 2026-11-05 | 963dc83e plus final D1 source | Lane aggregates, seven actual dispatch reasons, D1 eligibility evidence, per-generation emission bound, compact fresh fit accounts |

## Execution log

- Planning amendments preceded implementation. The owner subsequently requested
  implementation per this plan; the supervised implementation starts from final
  completed D1 and preserves unrelated staged coalescing/dependency work.

### Amendment-only verification result (historical)

Different-family follow-up review: PASS. It verified the amended compact
accounts/comparison, continuity keyed by barrier/lane/causal identity rather
than reason code, alternating unavailable/exhausted observations without age
reset, one forced append per due generation, nullable source witness and
honest owner-indexed count basis. Reason changes are semantic publication
changes; count/head/usage/retry-clock churn alone is not. No new blocking
technical issue remains in this plan amendment.

Plan-only checks: four plan-metadata tests passed at normal concurrency;
`bin/check-doc-paths`, `bin/check-dom15-fixtures`, `bin/coalesce-check` retrieval
cues and `git diff --check` passed. This is no runtime/scaling acceptance.
At that amendment boundary the plan remained draft and indexed; no runtime
code, active specification, queue data, recovery verb or commit had changed.

- Implementation first slice: user authorized implementation per plan. RED
  actual Manager turn failed at missing `dispatch_refusals` snapshot field.
  The complete-snapshot, delayed-standing, retained-custody and stable-pass
  regression now passes. No authority flag is changed by diagnostic publication.
  Pure conversion is shared across Manager snapshot readers and CLI surfaces;
  the only reason descriptor table lives in `_constants.py`.


## Implementation closeout

The owner's subsequent request authorized implementation. All four reviewed
Replacement blocks were applied with the runtime/public wiring. Promotion
baseline: `963dc83e` plus completed D1 and this implementation's worktree delta.
No new queue, registry field, dispatch permission, retry timer, replay action or
operator verb was introduced. Unrelated staged coalescing, libc and dependency
work was preserved; no commit was requested.

### Source and review findings

- A different-family source review initially returned BLOCKED. Signature and
  property allegations were rejected against actual imports/decorators: the CLI
  calls the commands-layer ManagerSnapshot wrapper, and the scheduler accessor
  is a property. AdmissionController derives both lane decisions from one
  coherent global capacity sample; the actual public-denied/internal-allowed
  transition verified that the shared observation is valid. A request's source
  broker ID is its immutable TID. No redundant per-lane model was added for
  those false premises. Unavailable diagnostics may retain last-observed items;
  they are not a known-open verdict.
- Accepted classification repair gap: the repair loop did not record its
  failing request. RED used an original-ID returned row below the classification
  watermark, with an older indexed source head. Its actual pending-index write
  fault produced no classification refusal. The fix records repair-loop causes
  and reuses completed bounded primary/repair page ranges and existing index
  membership as classification/positive-absence evidence. It adds no point
  read or source scan. The regression proves both establishment and clearing
  while the older indexed source head remains.
- Broad owner testing found a real configured-lane error: custom-inbox Managers
  have no internal_inbox, and the initial collector indexed that absent key.
  Existing spawn/drain/custom-inbox cases failed with that exact KeyError. The
  collector now uses configured lanes only; STOP/drain uses existing lifecycle
  state rather than diagnostic assessment. Targeted regressions passed.
- The added PG startup-standing exercise froze the Manager clock. An existing
  fixture helper aged its launch using a different real clock, defeating its
  own fallback setup. It now uses the governing Manager clock. No production
  timeout, worker count or admission policy was loosened.
- Round 2 independent source review withdrew the false premises, accepted the
  classification repair and returned PASS. Final integration review inspected
  the configured-lane/STOP guards and returned PASS with no new defect.
  Review artifacts: `/tmp/weft-d5-implementation-review.json`,
  `/tmp/weft-d5-round2-review.json`, and
  `/tmp/weft-d5-final-integration-review.json`.

### Execution and firing evidence

- All seven actual reason owners have executable transition proofs, including
  startup and bootstrap/checkpoint faults. Suppressing each reason in an
  isolated copy made its owning proof fail at the required observation; all
  seven controls exited 1 with one intended failure. Removing delayed standing
  made the normal-startup proof fail its observable no-refusal assertion.
  Replacing the complete diagnostic body with a refusal-only mapping made the
  two-crash proof fail at its required Manager role. No control changed this
  working checkout or remained in production.
- Actual stable resource retry passes assert diagnostic append counts [1,0,0].
  Alternating unavailable/exhausted observations assert [1,1,1] without resetting
  standing age. Six genuinely changing resource generations assert at most one
  diagnostic append per generation; incidental turns add zero. Counts/head/
  usage changes alone do not publish. The null-witness case keeps known indexed
  work visible after an initial source-head read failure.
- Count honesty was tested explicitly: after another dispatcher removed one
  source row and two rows arrived, the owner index still held six entries for
  five actual source rows. The test preserves that permitted lag, the
  manager_indexed basis and the unchanged last-published snapshot. It does not
  turn diagnostics into a hidden whole-queue count.
- At backlogs 4 and 8, the zero-live-resident serialization phase performs no
  queue read/stat/enumeration and has fixed lane shape without queued subjects.
  A subsequent actual complete task drain counts D5 field bytes in normal
  checkpoints. Added serialization is bounded per checkpoint; existing live
  resident cost is separate. This is not a CPU benchmark or an O(1) claim for
  complete snapshot publication.
- A real task-owned snapshot with unknown creation time removes receipt-missing
  observation while retaining the exact reservation. Actual diagnostic append
  failure retains the preceding complete snapshot and retries only on a later
  due pass. Two real Manager process deaths followed by a third owner preserve
  the first exact reservation; successful diagnostics do not complete failed
  bootstrap or clear required checkpoint responsibility.
- Public readers use the matching task-state source, project status reuses its
  captured map, IDs above 2**53 stay exact, four causal references produce three
  human samples plus totals/--json, and failed selected-state reads are
  unavailable. No raw-history/Monitor-private dependency was introduced.

### Gates

- Focused PG owner gate: 281 passed in 104.95 seconds.
- Full all-tests SQLite: 6637 passed, 60 skipped, 464.27 seconds.
- Full --all PostgreSQL: 6585 passed, 40 skipped, 774.10 seconds.
- Django/Docker/macOS sandbox/microsandbox roots: 385 passed, one PG-target
  skip, 28.62 seconds.
- Canonical mypy: 492 source files. Final Ruff check/format: 638 files. Doc paths,
  DOM-15 fixtures, coalescing retrieval cues and diff checks passed. Foreign SHA
  claims remain explicitly foreign, not falsely verified locally.
- Original full fast SQLite: 6632 passed, 49 skipped, one STOP-test miss,
  474.60 seconds. The entire failing case took 28.203 seconds, contradicting a
  proposed 120-second natural-expiry explanation. The unchanged case passed a
  focused rerun and three isolated diagnostic repetitions; the full all-tests
  SQLite superset also passed it. This establishes intermittence, not root
  cause or proof of a preexisting defect. No STOP assertion/budget was relaxed.
  Failure-only pre-cleanup snapshot/terminal/control-queue capture was added
  before repeating the original full fast command. The full fast repeat exercised the STOP case successfully: 6630 passed,
  49 skipped, with three policy failures all caused by the diagnostic addition's
  unsorted import group (314.31 seconds). That import-only defect was corrected;
  Ruff and all 165 specification/policy tests then passed in 5.27 seconds. The
  repeat is not reported as a wholly green command. The owner explicitly
  directed us to stop investigating this STOP case and watch for recurrence;
  no further STOP experiments, production edits, budget changes or retries
  followed that instruction.
- Final closeout metadata/spec checks: 4 plan metadata tests passed in 1.04s; documentation paths, DOM-15 fixtures, coalescing cues, and `git diff --check` passed.

### Theory-versus-code audit

Unchanged rows carry forward by reference from the program-theory reconciliation
and completed D1 audit. This refresh uses committed [REV-WT-006].

| Claim | Contract and current evidence | Disposition |
| --- | --- | --- |
| [WT-2a.15] scoped visible standing refusal | [MA-1.8], [MF-5], [CLI-1.2.1]; seven owner transitions, actual complete publication, public queue-to-status conversion, reason ending assertions | aligned for the enumerated current dispatch gates; no operator recovery promise |
| [WT-2a.2] owner facts | [OBS.6], [OBS.6a]; complete diagnostic snapshots, selected/captured source reads, no registry copy | aligned for changed observation boundary |
| [WT-2a.6], [WT-4.1] one reactor/fact-based wake | [MA-1.8]; one due generation counter, existing maintenance opportunities, zero builder source reads, no new waiter/timer | aligned for diagnostic firing and ownership |
| [WT-4.3] quiet and bounded added cost | [MF-5]; [1,0,0] stable writes, bounded flapping writes, 4/8 source serialization/drain proofs | append/read/added-field bounds measured; CPU and whole-snapshot constant cost unmeasured |
| [WT-2a.7], [WT-2a.13] custody and stronger death proof | MANAGER.11; real two-crash preservation, required-checkpoint/diagnostic separation, unscoped started evidence without ACK | aligned for changed publication/custody boundary |

The earlier STOP oracle miss remains an explicit verification limit; this audit
makes no new whole-program STOP alignment claim or unproved D5-causality claim.

### Final annotation and implementation fit

A final source-scope check found that internal no-receipt records were described
as public family paths. A real internal source/reservation/checkpoint bootstrap
case failed at the lane assertion. Annotation now derives from retained
source_queue: internal requests report internal/request; finite public requests
report public/family_path. This changes no D1 selection, dispatch, timer, ACK or
custody rule. The narrow case passed SQLite (1 test, 1.12 s) and PostgreSQL
(1 test, 1.50 s). Broad gate results above precede this annotation-only change;
current canonical mypy492, Ruff and format638 passed afterwards. The pure
module header also names its governing spec paths. No broad runtime rerun was
needed for these metadata/documentation changes.

Fixed theory: `963dc83e` ([REV-WT-006]). Final D5 runtime plus four normative
blocks: SHA-256
`eecfd39161efbdf895fdce35b70bbc2b181e6fa3126be42a5d93ef2654eabab3`.
The D5-only diff excludes earlier D1, unrelated libc/dependency/coalescing edits,
plan/review text and author account. Its source baseline was reconstructed from
HEAD plus the saved final D1 source patch; the constants boundary is only the
D5 descriptor addition, and normative changes are the four exact D5 blocks.
Superseded runtime trial `182fd1ffb5f770dd715c51674a37812a6faac967024d9fa33c03e23abe14fd1f`
and earlier trial artifacts are historical, not evidence for byte-identical
final source. The final private author account was fixed before reading the
fresh Google-family final account. The isolated independent context received
only fixed theory and exact diff, excluding this plan, logs and author account.
Both current five-part accounts are preserved verbatim below.

Both accounts say fits with strain. The author emphasizes extra concepts; the
independent account emphasizes aggregates and bounded human sampling. This is
an accepted presentation/cost tension, not a change or document deficiency:
resource measurements describe the actual barrier; queued affected requests
are not themselves all causal records; held ancestors/custody references remain
complete in JSON. An inference that every affected queued ID must be enumerated
or that human sampling discards the JSON causes is classified as contested
agent reasoning. No theory edit follows. The earlier trial's operator-action
concern remains an accepted operational limit, not a promise of recovery:
missing evidence can leave a hold unresolved, and no new operator verb exists.
Final account artifacts: `/tmp/weft-d5-final-scope-author-fit.txt`,
`/tmp/weft-d5-final-scope-implementation.diff`, and
`/tmp/weft-d5-final-scope-fit.json`.

Author account (verbatim):

```text
Verdict: fits with strain.
Sections: WT-2a.2/6 keep the complete observation with the Manager and its reactor; WT-2a.15 requires actual scoped standing refusals and ending evidence; WT-2a.7/13 retain exact custody and stronger death proof. WT-4.1/3 require queue facts, observable quiet and measured costs.
Refusals: ALT-WT-009/020 reject global lifecycle-log reconstruction and Monitor-private status; ALT-WT-002 rejects another retry timer; ALT-WT-016 rejects repeated source/graph scans; ALT-WT-019 rejects an unconsumed shadow table. This adds no execution authority, registry copy, replay or operator-release API.
Predictions: real due saturated retries produce diagnostic append counts [1,0,0]; reason flips preserve resource-barrier standing age and force at most one append per due generation. Counts, usage and head churn alone add no write. Snapshot construction reads no source and adds no queued-subject array; witnesses are bounded to one read per refused lane/pass. Status preserves IDs above 2**53 from the matching captured snapshot and renders three causal samples with complete JSON. Two actual Manager deaths preserve the first reservation. Unknown process creation time can clear a receipt refusal without ACK or death inference.
Worst-fitting sentence: WT-4.4, “As few concepts as possible.” A generation, continuity keys, classification cause and cached witness observation add concepts. They fit only as a derivative of actual dispatch decisions, with no shadow scheduler, no diagnostic permission and explicit index-basis/staleness. Last-published observations can lag reality; unreadable observations are unavailable, never evidence that dispatch is open. A dead owner can fail to publish and permanent missing receipt evidence can remain unresolved; neither becomes magical recovery.

Final boundary refinement: lane collection follows only configured queues, and STOP/drain uses existing lifecycle state rather than diagnostic work. A custom-inbox Manager need not own an internal lane. This preserves WT-2a.15 actual scope; it grants no additional dispatch authority.

Final scope annotation: an internally sourced retained request is reported as lane internal/scope request, not as a public family path. The source queue already present in its exact custody record supplies that annotation. This changes no dispatch or custody transition; it makes the published actual scope honest under WT-2a.15.
```

Independent account (verbatim):

**Verdict:** Fits with strain.

**Sections:** Rests on `[WT-2a.15]` (standing refusals published as scoped queue facts naming reasons, causal records/barriers, and ending conditions in ordinary status), `[WT-2a.1]` (lossless ID formatting via `format_message_id`), `[WT-2a.2]`/`[WT-3]` (carried on `weft.state.tasks.<manager_tid>`), `[WT-4.3]` (evaluated on existing maintenance generations without new timers), and `[WT-4.4]` (shared CLI/client `ManagerSnapshot`).

**Refusal:** Refuses `[ALT-WT-016]` (avoids $O(N)$ queue scans by reporting `indexed_pending_count` and one `peek_one` witness), `[ALT-WT-002]` (no diagnostic polling timers), `[ALT-WT-010]` (no separate state store), and `[ALT-WT-020]` (no `MonitorStore` reads).

**Falsifiable prediction:** Holding a dispatch gate closed across two consecutive maintenance generations (`_dispatch_generation`) emits one `weft.state.tasks.<manager_tid>` snapshot whose `dispatch_refusals.items` lists the barrier and `ends_when`, performs at most one `peek_one` per refused lane per pass, and writes zero additional snapshots across subsequent passes while `refusal_semantics` remain unchanged.

**Worst-fitting sentence and tension:** `[WT-2a.15]`: *"When a Manager declines to dispatch accepted work for longer than one maintenance pass, the refusal, its reason, and the records that cause it are published as a queue fact that an ordinary status read shows."* **Tension:** `dispatch_lane` items report a lane aggregate (`oldest_request` witness, `known_pending_count`) instead of causal records, and human `status` truncates causal `subjects`/`evidence` to three items—straining `[WT-2a.15]`'s literal record-reporting rule to preserve `[ALT-WT-016]`'s bounded-cost invariant.

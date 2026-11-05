# Scoped Dispatch Around Ambiguous Launches

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.6], MANAGER.11, MANAGER.18
Superseded by: none

Class: 5, risky scheduling/custody change. Promotion strategy B: code, tests and
exact normative delta land together. Promotion baseline: `963dc83e` plus this completed, uncommitted worktree delta;
all exact Replacement blocks are applied and verified.
Baseline: `963dc83e` plus the uncommitted
wrapper-lost idempotence repair, whose behavior is a constraint, not this scope.
Theory baseline: current committed [REV-WT-006], including [WT-2a.15]'s explicit
custody/durability exception and the revised [WT-7] two-factor fit test.

## Goal and scope

Implement owner decision D1: a positively retained pre-launch reservation with
no process receipt stays held and is never replayed, but it no longer stops
unrelated dispatch. Preserve measured admission and actual checkpoint-durability
barriers. Hold descendants whose immutable ancestry includes the ambiguous TID;
unrelated roots and sibling branches remain eligible. This is a scheduling
eligibility partition over existing queue-backed requests, not a second lane.

D5 publication, status fields, operator settlement verbs and operator declarations
of death are out of scope. This plan closes only the dispatch-scope half of
[WT-2a.15]; the published-refusal half remains drifted under the separate D5 plan.
No TaskSpec/queue/schema/CLI/dependency change is proposed.

## Source and causal premise

Read program theory, agent context and testing/review runbooks. Current owners:
`Manager._recover_scheduler_paths` puts every inherited no-receipt resident in
`_scheduler_undurable`; `_queue_has_pending` and `_process_queue_message` then
gate every ordinary dispatch. The same set also protects genuinely failed
post-launch checkpoint publication (`_checkpoint_spawned_admission`), so merely
removing its checks would weaken launch custody.

`_retry_stale_child_launches` can reach the same receipt ambiguity when its
worker result is lost. `_startup_feedback` is a bounded fresh-launch gate,
not a permanent charge for ambiguous process usage. A missing receipt must not
become an enduring startup-window permit charge.

`FamilyScheduler` already keeps request IDs and lineage, per-parent LIFO indexes,
per-family FIFO indexes, and the contracted resident frontier. Selection must
exclude the held ancestry closure without repeatedly scanning a held FIFO prefix
on each sibling launch. Source requests and pending tokens remain unchanged.

## Invariants and hidden couplings

- [WT-2a.7], MANAGER.11: no replay, requeue, terminal inference or acknowledgement
  follows from missing evidence. Original checkpoints/reservations retain custody.
- [WT-2a.13]: no-process evidence is unknown, never a death verdict.
- [MA-1.8], MANAGER.18: failed real checkpoint publication still gates ordinary
  work until existing durable evidence succeeds; measured admission is unchanged.
- Startup feedback ends for a launch classified as ambiguous only after its
  in-flight launch worker is no longer active. It does not end while a fresh
  launch is still collecting its normal bounded startup receipt.
- Family ordering is unchanged among eligible requests. A held ancestor also
  holds descendants; siblings and unrelated families may proceed.
- Pending source and label rows remain durable and unconsumed while held.
  Eligibility state is rebuilt from queue-backed residents on every successor.
- Hold/release invalidates the Manager's cached family candidate. No timer,
  fixed lookahead window, new queue, inferred capacity charge or polling cap.
- Complexity: classification checks immutable ancestry. A hold-set change makes
  one pass through known pending requests, with existing logarithmic heap edits
  for requests that change partition. This work occurs only on hold changes;
  ordinary selection retains existing heap/frontier costs and does not
  repeatedly scan held prefixes.

## Exact proposed spec delta

### [MA-1.8] in docs/specifications/03-Manager_Architecture.md

First replace the preceding sentence beginning "Before another ordinary
 dispatch" and ending "bounded maintenance" with:

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
A launch with known post-launch process evidence must preserve recovery custody through task-owned snapshot handoff/positive settlement or a successful post-launch checkpoint before another ordinary dispatch; failed required checkpoint publication gates dispatch and retries through bounded maintenance. A retained no-receipt launch follows the scoped hold rule below.
```

Then replace the sentences beginning "If a retained pre-launch reference" and
ending "outside the conditional drain guarantee" with:

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
If a retained pre-launch reference has no durable post-launch process receipt, a successor preserves its validated identity, unresolved reservation, and finite frontier where applicable. The ambiguous launch and accepted requests whose immutable ancestry includes its TID remain held; unrelated roots and sibling branches may dispatch under the normal measured admission and frontier rules. A validated task-authored snapshot matching the immutable runtime identity and reporting a started process, or a durable post-launch process receipt, ends the scheduling hold. A snapshot with an unknown process creation time may establish started evidence for eligibility but does not authorize custody acknowledgement or prove process death; missing evidence never authorizes replay or terminal inference. A failed required checkpoint publication remains a separate ordinary-dispatch barrier until durable custody is established. A fresh in-flight launch retains its startup-feedback gate, but a retained ambiguous launch does not occupy that transient observation window indefinitely. When no resolving evidence ever arrives, the request stays held; no supported manual-release operation is introduced here. Such held work is outside the conditional drain guarantee.
```

Append one implementation note to [MA-1.8]:

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
Family scheduling partitions known pending requests into eligible and held in-memory indexes according to retained ambiguous ancestors. Holding a request does not consume its source row or pending token. Hold-set changes reclassify known pending requests once; normal selection does not repeatedly scan a held request prefix. Successor recovery rebuilds this derived eligibility from retained custody and lineage.
```

### [MF-5] in docs/specifications/05-Message_Flow_and_State.md

Replace the identical retained-pre-launch span beginning "If a retained pre-launch reference" and ending "outside the conditional drain guarantee" with:

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
If a retained pre-launch reference has no durable post-launch process receipt, a successor preserves its validated identity, unresolved reservation, and finite frontier where applicable. The ambiguous launch and accepted requests whose immutable ancestry includes its TID remain held; unrelated roots and sibling branches may dispatch under the normal measured admission and frontier rules. A validated task-authored snapshot matching the immutable runtime identity and reporting a started process, or a durable post-launch process receipt, ends the scheduling hold. A snapshot with an unknown process creation time may establish started evidence for eligibility but does not authorize custody acknowledgement or prove process death; missing evidence never authorizes replay or terminal inference. A failed required checkpoint publication remains a separate ordinary-dispatch barrier until durable custody is established. A fresh in-flight launch retains its startup-feedback gate, but a retained ambiguous launch does not occupy that transient observation window indefinitely. When no resolving evidence ever arrives, the request stays held; no supported manual-release operation is introduced here. Such held work is outside the conditional drain guarantee.
```

### [OBS.6] in docs/specifications/07-System_Invariants.md

Replace the identical retained-pre-launch span beginning "If a retained pre-launch reference" and ending "outside the conditional drain guarantee" with:

Replacement (`docs/specifications/07-System_Invariants.md`):

```text
If a retained pre-launch reference has no durable post-launch process receipt, a successor preserves its validated identity, unresolved reservation, and finite frontier where applicable. The ambiguous launch and accepted requests whose immutable ancestry includes its TID remain held; unrelated roots and sibling branches may dispatch under the normal measured admission and frontier rules. A validated task-authored snapshot matching the immutable runtime identity and reporting a started process, or a durable post-launch process receipt, ends the scheduling hold. A snapshot with an unknown process creation time may establish started evidence for eligibility but does not authorize custody acknowledgement or prove process death; missing evidence never authorizes replay or terminal inference. A failed required checkpoint publication remains a separate ordinary-dispatch barrier until durable custody is established. A fresh in-flight launch retains its startup-feedback gate, but a retained ambiguous launch does not occupy that transient observation window indefinitely. When no resolving evidence ever arrives, the request stays held; no supported manual-release operation is introduced here. Such held work is outside the conditional drain guarantee.
```

### [MF-6] in docs/specifications/05-Message_Flow_and_State.md

Replace the bullet beginning "after exhausted recovery" and ending
"older exact request needs manual recovery" with:

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- after exhausted launch-receipt recovery, the retained ambiguous request and its ancestry-dependent work remain held without replay; unrelated roots and sibling branches remain eligible under normal admission and frontier rules. Genuine checkpoint-durability failures keep their existing dispatch barrier. Missing receipt evidence alone does not occupy the transient startup-feedback window indefinitely or authorize terminal settlement
```

No diagnostics/publication rule is removed. D5 supplies the published-refusal
contract independently; this slice does not claim that rule is implemented.

## Tasks

1. Before implementation: independent cross-family plan review. Author's theory
   fit account is kept separate from the plan until the blind account is written.
   Refresh both over one final diff/theory boundary at completion per [WT-7].
2. RED broker/process proof: retain an actual pre-launch checkpoint, kill/retire
   its owning Manager without a process receipt, start a successor, submit a new
   independent root and observe real dispatch. The held exact reservation and
   checkpoint remain. Repeat across a second successor to prove reconstruction.
   Update the existing family-matrix test whose old contract expects a global
   stall; keep its unknown-frontier and no-replay assertions.
3. Implement a pure scheduler eligibility partition. Add a held-ancestor set and
   parked pending-request map alongside the existing eligible `_pending` map.
   `pending(id)` consults both, preserving idempotent classification/repair;
   `add_pending` parks a request whose ancestry intersects the holds; removal
   forgets either partition. Hold changes move requests through existing index
   add/remove operations without touching broker rows; release restores the same
   request/label identities and ordinary FIFO/LIFO order. Keep valid frontier
   residents, including ambiguous ones. Unit cases cover held descendants,
   same-family siblings, multiple holds, exact removal while held and resumption.
   Repeated hold/release cycles prove unique request IDs and bounded physical
   index sizes using the existing indexed heap, without adding compaction.
4. Manager classifies receipt ambiguity separately from `_scheduler_undurable`.
   Bootstrap no-receipt residents install scheduler holds instead of adding an
   undurable-publication barrier. An abandoned launch worker with no receipt
   first reconciles available task snapshots, then takes the same scoped hold only
   if evidence remains absent after leaving active launch ownership. Move its
   existing startup-feedback observer into held observation membership: retain
   late state-queue wakeups and cursor state without charging a fresh-launch
   permit. All observer iteration and cleanup cover both memberships. A real process
   receipt with a failed checkpoint remains `_scheduler_undurable`; do not remove its existing gates.
   Matching task-authored started evidence (including unknown process creation
   time) or a committed post-launch receipt releases the scheduling hold separately
   from the stricter custody acknowledgement rule. A late unscoped snapshot
   releases eligibility but retains its reservation. Release removes the
   corresponding scheduler hold and invalidates cached selection. Positive
   settlement clears any remaining derived hold. Test every transition, including
   a real checkpoint-write fault that still blocks a new ordinary reservation.
5. Firing closure and scaling proofs: a held descendant plus eligible sibling
   and new root; no held source/label consumed; release restores correct order;
   held-only input remains quiet through the real watcher on both backends;
   repeated eligible selection does not inspect every held request. Enable INFO
   logging for a real held-only watcher proof: a held source is not dispatchable
   backlog and must retire the public-stall diagnostic deadline. Service or
   retire an already armed diagnostic stamp from its owning process turn even
   when the ordinary source is no longer active; never leave a permanently
   expired logging deadline or add a replacement timer. Use existing
   real broker/process fixtures and count at the responsible owner boundary.
6. Promote exact spec delta, synchronize implementation mappings/docstrings and
   CHANGELOG, then full normal-concurrency SQLite/PG gates, canonical static/doc
   checks and independent completed-work review. Carry unchanged audit claims
   forward; mark dispatch scope aligned only after running the new proofs and
   leave D5 publication drift explicit. Finish plan/index status together.

## Hardening, rollback and rollout

One current Manager owns dispatch. No queue format or migration is needed;
restart reconstructs holds from existing durable evidence. Rollback restores the
old global stall and does not replay held work. Preserve task-state and reserved
rows during rollout. Stop for any proposal to ACK/replay an ambiguous request,
clear a genuine durability gate, charge guessed resources, add a lane/timer, or
change operator-visible D5 surfaces. Fault the actual checkpoint boundary, not
Manager scheduling or process liveness. Test resources have owner cleanup from
acquisition onward, including assertion failure.

## Verification

Load `. ./.envrc`; use normal configured concurrency throughout.

- SQLite scheduler, admission recovery, family-maintenance, family-matrix and
  source-reconciliation suites, including real process/custody cases.
- Same shared proofs through `bin/pytest-pg`.
- Full repository fast suite and full supported PG suite; relevant slow cases.
- Canonical mypy; Ruff check/format; tests/specs; doc paths and diff checks.
- Cross-family plan review before code and completed-work review before closeout;
  blind [WT-7] fit account supplied only theory and final non-plan diff.

## Review, deviations and execution log

- Author causal pass: global gate conflates missing receipt with unsuccessful
  durability publication; permanent startup-feedback membership is a second
  coupling. Existing family indexes must partition eligibility once per hold
  transition, preserving queue identities rather than filtering a heap head in
  an unbounded per-dispatch loop.
- Independent review, RED/GREEN measurements, fit comparison and final audit
  refresh are completed below. The owner's D1 request supplied implementation
  scope authorization; plan status alone did not.

- Pre-code self-review and blind-fit disposition: qualify the old globally
  worded post-launch durability sentence so it cannot contradict the scoped
  no-receipt rule. Replace the inherited operator-resolution phrase with an
  honest statement that this slice introduces no supported manual release.
  Heap repartition work is described as a single pass plus normal index-edit
  costs, not an unsupported linear-time claim.

- Cross-family pre-code review: accepted missing duplicate contract deltas in
  [MF-5]/[OBS.6], the held-only public-stall logging deadline defect, reconcile-
  before-hold ordering, and continued late-snapshot observation. Exact deltas
  above cover all three repeated custody paragraphs. Positive started evidence
  ends scheduling refusal independently of the stricter scoped custody ACK.
- Rejected the proposed new `_FamilyPending` heap compactor: current
  `weft/core/family_scheduling.py::_FamilyPending` uses an indexed array heap
  (`ids` and `positions`) with exact removal, not a lazy leftist heap. Existing
  `_PendingIndex` has its own compaction. A repeated partition-cycle regression
  will verify reuse and bounded storage instead of introducing another mechanism.
- Independent review round 2 accepted these corrections before runtime
  implementation. The earlier blind-fit account covered the initial boundary;
  the completed non-plan diff account and comparison are recorded below.

- Independent cross-family plan review initially blocked on four concrete gaps.
  Round 2 `/tmp/weft-d1-plan-round2.json` returned SUCCESS/PASS after exact
  duplicate spec deltas, logging-clock ownership, continued late observation,
  and the scheduling/custody distinction were specified. The indexed-heap
  allegation was withdrawn after inspecting actual source.
- RED real crash proof: readiness was `[False]` for the independent root after
  successor recovery, failing "ambiguous custody blocked an unrelated root".
  Scheduler partition REDs failed at absent hold API.
- Core and real-process proof pass: 720 passed, 3 backend skips, one remaining
  old fresh-feedback membership assertion (now updated to the approved held
  observation contract). The real matrix covers a retained descendant and two
  successors without replay; all five matrix cases pass. The fixture's clean
  checkpoint retirement barrier now checks both public and internal reserved
  routes, so an unfinished heartbeat cannot be mistaken for empty custody.
- Periodic reconciliation keeps held launches in the bounded resident rotation;
  only the bounded fresh startup membership is promoted into each batch. Held
  state publications independently wake their existing observation cursors.

## Completed-work review and verification

- Independent completed-work review used Google-family agy plan/sandbox mode.
  The first native source-read attempt was denied and supplied no verdict.
  The source-inline retry reviewed the actual diff, full scheduler and Manager
  owners. Its sole P1 alleged that unknown `create_time` was discarded and that
  the test omitted dependent-child selection. Both premises were false:
  `_admission_host_processes` preserves positive PID records with time `None`,
  adoption merges that direct list before scoped RunnerHandle observations,
  and the same test submits and selects a dependent child after proving the
  reservation remains. Source-backed counterevidence review returned
  SUCCESS/PASS, explicitly withdrew P1 and found no new defect. Artifacts:
  `/tmp/weft-d1-completed-review-inline.json` and
  `/tmp/weft-d1-counterevidence-review.json`. No redundant validation helper or
  runtime workaround was added for the false finding.
- Final test-only refinements removed the old post-submit readiness witness
  (a fast launch could beat witness assignment), retaining actual leaf/result
  outcomes instead. Late scoped and unscoped snapshots now drive the actual
  reactor publication path while periodic maintenance is fenced. Required
  checkpoint write faults still gate admission, and unknown creation time still
  retains the exact reservation. Both brokers ran these final tests.
- Final wording clarification, approved by the supervising agent: a durable
  positive post-launch PID receipt is started evidence even when creation time
  is unknown. Scheduling release therefore says "post-launch process receipt";
  scoped identity remains required for custody ACK and death. This aligns the
  ending sentence with its no-receipt premise and existing source behavior;
  it changes no runtime behavior and requires no new mechanism.
- SQLite full fast run: **6617 passed, 49 skipped, one metadata failure** in
  255.79 seconds. The failure was the promoted plan's still-draft index row,
  corrected narrowly. The final matrix/family-maintenance/spec regression run
  then passed **203 tests in 39.71 seconds**, including metadata agreement.
  No runtime source changed after the full run. Relevant SQLite slow Manager
  proofs passed **3 tests in 32.55 seconds**.
- SQLite focused owner gate: **721 passed, 3 expected backend skips**, 46.40 s.
  PostgreSQL focused owner gate: **715 passed, 12 backend skips**, 145.67 s.
  Final full supported PostgreSQL gate: **6470 passed, 40 skips**, 360.31 s,
  at normal configured concurrency. No serialization or reduced worker count.
- Canonical mypy: **491 source files**; Ruff check and format: **668 files**;
  doc paths and diff checks passed. After the final status/index flip and
  receipt wording clarification, all **165 specification tests** passed in
  **3.97 seconds**; doc paths and diff checks passed again. No additional broad
  runtime rerun is needed for log-only edits.
- No new commit was requested for this follow-up; source, tests and docs remain
  a reviewable worktree delta. D5 is drafted separately and is not implemented.

## Theory-versus-code audit refresh

Unchanged rows carry forward from the complete audit in
`2026-11-02-program-theory-reconciliation-plan.md` and the touched-row refresh in
`2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md`. This refresh
uses [REV-WT-006]; it does not revive the superseded owner-keyed probe.

| Claim | Contract and running evidence | Disposition |
| --- | --- | --- |
| [WT-2a.7] reservation custody and no replay | MANAGER.11, [MF-5]; real postlaunch crash, two successors, original start count **1**, exact old reservation retained, **2** independent roots complete | aligned for repaired recovery boundary |
| [WT-2a.13] absence does not prove death | [MA-1.8], [OBS.6]; scoped/unknown creation-time late publication variants; unknown-time reservation remains; existing whole-process exit guards pass on both backends | aligned for changed boundary |
| [WT-2a.15] scoped refusal | [MA-1.8], [MF-5], [MF-6], [OBS.6]; accepted descendant launches **0** while held, unrelated completions **2** across successor reconstruction; pure sibling/multiple-hold cases and 10,000-entry no-prefix-scan guard pass | dispatch scope aligned; publication remains drifted to `2026-11-05-manager-published-refusals-plan.md` |
| [WT-4.1] queue-fact wake and firing closure | [MA-1.8]; final real late snapshot notification drives reconciliation without periodic maintenance; held-only source/label remain unconsumed; admission-blocked/new-source proofs pass | aligned for changed wake paths |
| [WT-4.3] observable quiet | [MA-1.8]; INFO held-only watcher blocks for at least **250 ms** in the 300 ms observation window after retiring the armed diagnostic stamp, both backends | wait behavior measured; held-only CPU remains unmeasured. Carry forward the earlier supervising CPU probe without extrapolation |
| [WT-2a.7], [WT-2a.13] terminal settlement | [MA-1.6a]; actual killed process, both local-cleanup/retirement orderings: exactly **1** terminal envelope with actual POSIX **-9**, malformed unrelated manager rows do not suppress settlement | aligned for repaired wrapper settlement |

## Two-factor fit accounts and comparison

The author wrote the account privately before reading the independent account;
explicit citation clarification was appended before reading its output. The blind
Google-family context received only [REV-WT-006] theory and the actual combined
D1 plus wrapper-idempotence production/normative diff, excluding plans, tests,
review logs and the author account. Its input is preserved at
`/tmp/weft-d1-implementation-fit-prompt.txt`; its response at
`/tmp/weft-d1-implementation-fit.json`. The fixed reviewed snapshot preceded the
semantics-preserving immutable-field `any(...)` refactor and the receipt wording
clarification above; neither is falsely represented as byte-identical to that
input. Completed-work source review and final gates cover the final source.

Both valid five-part accounts say **fits with strain**. The publication gap is a
**change deficiency, accepted as explicit partial drift** closed by D5. The blind
account additionally treats lack of an operator escape as a contract strain.
The author classifies that claimed contract gap as **contested agent reasoning**:
[WT-2a.15] says evidence **or** operator action, and this change names resolving
evidence. Its operational observation remains true and accepted: evidence may
never arrive, so accepted work can remain held indefinitely. No manual-release
API is implied. The blind response also misnames the retirement owner as
`_recover_unhanded_launches`; actual ownership is `_retire_scheduler_residents`.
That source-citation error is recorded, not treated as independent validation.
No theory document deficiency or theory revision is claimed.

### Author account (verbatim)

```text
Final author account, written before seeing the final blind account.
Boundary: actual combined scoped-dispatch D1 implementation plus the same-session wrapper-lost idempotence repair, and their normative spec deltas. No plan, execution log, test rationale, D5 publication, or unrelated dependency changes are part of this boundary.
Verdict: fits with strain. D1 realizes WT-2a.15's scope limit: retained uncertainty denies replay and the immutable ancestry-dependent closure, while unrelated roots and sibling branches retain measured admission and ordinary frontier order. Durable source rows and pending labels remain facts; held indexes are rebuilt from retained queued custody/lineage. Existing index remove/add operations implement the partition, and exact indexed heaps do not need another lazy-compaction layer. A real failed required post-launch checkpoint still blocks ordinary dispatch. Positive task-owned started evidence releases scheduling separately from the stricter scoped ACK/death boundary, including create_time=None. Fresh startup permits are bounded readiness observations, not a permanent guessed resource charge. Held task state retains notification interest; held-only source rows cannot keep a diagnostic clock permanently expired. The wrapper fix preserves one already committed Manager terminal outcome with its OS exit code, while keeping task-owned terminal grace and local process ownership.
Predictions/falsifiers: an actual postlaunch no-receipt crash must retain its exact reservation and frontier across two successors, never rerun the original accepted request, hold an accepted descendant, and launch independent roots. Late unscoped task evidence must restore eligibility without ACK. Failed checkpoint writes must still prevent a new ordinary reservation. Repeated hold/release must preserve unique request IDs and bounded indexes; selecting unrelated work must not scan a large held prefix. An INFO held-only watcher must block rather than spin. Both live cleanup/retirement orderings must produce exactly one wrapper_lost envelope with the real exit code. A failure in any of these contradicts the relevant alignment claim despite spec prose.
Strain: WT-2a.15 also requires published refusal evidence. D5 remains a separate drafted plan and is not implemented by this diff; the full clause remains drifted at its publication dimension. No manual release operation exists here. Under permanent missing evidence, the exact accepted request and its dependent closure remain held indefinitely. Owner authorized that scope. Operational notification handles scale with retained held launches, using the existing cheap queue/observer primitive rather than another polling timer; periodic resident reconciliation remains bounded.
Rejected alternatives: global ambiguity stall violates locality; replay from absent receipts violates custody; inferred process death violates positive evidence; counting ambiguous launches as fresh-startup permits forever conflates resource authority with transient readiness. An extra lane, shadow store, guessed resource count, or new timer is unnecessary.
Explicit governing references: WT-2a.7 supplies the reservation/no-replay refusal; WT-2a.13 supplies the no-death-from-absence refusal; WT-2a.15 supplies the held ancestry boundary and publication tension; WT-4.1 and WT-4.3 govern queue-fact wakeups and observable quiet. ALT-WT-001 refuses a child lane and guessed per-task connection budgets; ALT-WT-002 refuses fixed polling caps; ALT-WT-019 refuses another unconsumed relational table. The worst-fitting sentence is WT-2a.15's requirement that refusal, reason, source facts and ending condition are published: D1 alone does not implement that publication.
```

### Independent account (verbatim)

### 1. Verdict

**Fits with strain.**

The diff aligns with the core custody, liveness, and scoped-refusal principles by converting a global ordinary-dispatch block on a post-spawn crash without a durable process receipt into an ancestry-scoped scheduling hold (`FamilyScheduler.hold` / `release` and `_held_launch_feedback`), while repairing a local/periodic `wrapper_lost` race and duplicate `wrapper_lost` terminal-envelope publication. However, it strains directly against **[WT-2a.15]** on two fronts: the standing scheduling hold is maintained in derived in-memory structures (`_held_pending`, `_held_ancestors`, `_held_launch_feedback`) rather than published as an explicit refusal queue fact naming the held closure, and the specification explicitly declares that when no resolving evidence ever arrives, *"the request stays held; no supported manual-release operation is introduced here"*, whereas **[WT-2a.15]** requires that every standing refusal name the evidence **or the operator action** that ends it.

---

### 2. Sections the Change Rests On

- **[WT-2a.15] (A standing refusal is a fact, and it is scoped):** Directly motivates scoping a retained no-receipt launch hold to the ambiguous TID and accepted requests whose immutable ancestry includes that TID (`FamilyScheduler.hold(tid)`), while allowing unrelated roots and sibling branches to dispatch under normal measured admission and frontier rules, and keeping a failed required checkpoint publication as a separate durability barrier.
- **[WT-2.5] & [WT-2a.7] (Two-phase reservation custody & Exactly once per reservation):** Governs retaining the original reserved request and checkpoint obligation when a pre-launch reference exists without a durable post-launch process receipt, forbidding replay when execution ambiguity exists, and requiring exact acknowledgement before dropping checkpoint references (`_ack_launch_custody`).
- **[WT-2.3], [WT-2a.13], & [WT-4.3] (Detached process topology, Independent lifetimes; death needs proof, & Availability and death are different questions):** Grounds the rule that missing a PID or snapshot after a pre-launch checkpoint is `unknown` rather than proof of non-launch or death, that a snapshot with an unknown process creation time can establish started evidence for scheduling eligibility without authorizing custody acknowledgement or proving process death, and that periodic recovery (`_recover_unhanded_launches`) must not race local child cleanup (`tid in self._child_processes`) or emit duplicate `wrapper_lost` envelopes when terminal evidence is already visible (`_terminal_envelope_visible(..., include_wrapper_lost=True)`).
- **[WT-2.2], [WT-4.1], & [WT-4.2] (Queues carry durable facts; processes carry ephemeral hints; Consume-to-disappear queues; Classical CS models over blunt ad-hoc fixes):** Supports holding ancestry-dependent requests in an in-memory partition (`_held_pending`) without consuming or bulk-moving the underlying `weft.spawn.requests` rows or `weft.state.labels.<root_tid>.pending.<parent_tid>` tokens, rebuilding the hold set on successor bootstrap from durable custody and immutable `ancestry_tids`, and releasing the transient `_startup_feedback` readiness window into `_held_launch_feedback` so a retained ambiguous launch still wakes the reactor on late snapshot arrival without blocking unrelated startup admission.
- **[WT-2a.6] & [WT-4.1] (One arbiter per reactor & A wake predicate reads facts and guarantees progress):** Preserves reactor wake registration across both `_startup_feedback` and `_held_launch_feedback` (`_startup_observation_items`) and refines `_maybe_log_public_dispatch_stall` to check actionable classifier/candidate state rather than raw `public_inbox.has_pending()` rows that may merely sit behind held work.

---

### 3. What It Refuses and the Entry That Refuses It

- **Refuses bulk-moving or consuming pending tokens in `weft.state.labels.<root_tid>.pending.<parent_tid>` or re-scanning the held prefix on every dispatch turn when parking or releasing ancestry-dependent work** — refused by **[ALT-WT-016]** (*Re-derive the task graph on each dispatch or bulk-move pending tokens on parent exit*) and **[ALT-WT-019]** (*Using SimpleBroker queues as unconsumed relational tables or event logs*). Pending tokens stay unconsumed in their existing `.pending.<parent_tid>` queues until actual reservation, and `FamilyScheduler.hold` / `release` reclassify in-memory pending index entries once per hold-set transition rather than scanning held prefixes on every selection pass or mutating queue tokens.
- **Refuses treating Manager-local child handles or missing snapshots/receipts as proof that a pre-checkpointed task never launched or is dead, or replaying a reserved request on missing receipt evidence** — refused by **[ALT-WT-015]** (*Manager-local child tracking or OS `ppid` as the liveness/lineage source*, together with **[WT-2a.7]** and **[WT-2a.13]**).
- **Refuses leaving a leadership-yield or self-owner suspension-clear backdoor that drops an ambiguous launch fence after exhausted recovery** — the diff explicitly removes the spec clause in `05-Message_Flow_and_State.md` that previously allowed a manager to clear in-memory suspension as an "availability tradeoff", replacing that blunt global block-then-bypass with a permanent, ancestry-scoped hold grounded in **[WT-2a.7]**, **[WT-2a.15]**, and **[ALT-WT-006]** (no unverified requeue/replay of reserved work).

---

### 4. Falsifiable Prediction

1. **Scoped progress and exact hold recovery under an ambiguous launch:** If a Manager crashes after writing the pre-launch checkpoint for root or parent task $T_1$ (`host_processes` empty in `weft.state.tasks.<manager_tid>`) and before recording a post-launch process receipt or task snapshot, a successor Manager will:
   - retain $T_1$'s reservation and place $T_1$ in `_held_launch_feedback` and `_held_ancestors`;
   - hold every accepted child/descendant request whose `ancestry_tids` contains $T_1$ without deleting its `weft.spawn.requests` row or its `weft.state.labels.<root_tid>.pending.<parent_tid>` token;
   - continue dispatching unrelated root tasks and unaffected sibling branches with zero startup-feedback gate stall from $T_1$; and
   - immediately restore $T_1$'s descendants to ordinary scheduling eligibility within one reactor turn if a valid task-owned snapshot for $T_1$ (even one with an unknown `create_time`) is published to `weft.state.tasks.<T_1>`.
   - **Falsifier:** If an unrelated root task is blocked by $T_1$'s missing receipt (unless the checkpoint write itself failed and put $T_1$ in `_scheduler_undurable`), if a descendant of $T_1$ dispatches before started/receipt evidence for $T_1$ arrives, or if a pending token for a held descendant is consumed before dispatch, the change's claim is falsified.

2. **Idempotent `wrapper_lost` settlement without racing live child cleanup:** If a child wrapper exits without a task-authored terminal envelope while the launching Manager still tracks it in `self._child_processes` (or after a Manager has already committed a `source: "manager", error: WRAPPER_LOST_ERROR` terminal envelope to `T{tid}.ctrl_out`), periodic `_recover_unhanded_launches` will not race the local sentinel reap path, and repeated settlement calls to `_commit_wrapper_lost_envelope` will observe the existing manager-authored `wrapper_lost` envelope via `_terminal_envelope_visible(..., include_wrapper_lost=True)` and return `True` without appending a second terminal envelope to `T{tid}.ctrl_out` or `weft.log.tasks`.
   - **Falsifier:** Observing two `wrapper_lost` terminal envelopes for the same TID on `T{tid}.ctrl_out` or `weft.log.tasks`, or observing `_recover_unhanded_launches` retiring a child still present in `self._child_processes`, falsifies the repair.

---

### 5. Sentence That Fits Worst and Tension

> **Worst-fitting sentence in the theory (`[WT-2a.15]`):**
> *"When a Manager declines to dispatch accepted work for longer than one maintenance pass, the refusal, its reason, and the records that cause it are published as a queue fact that an ordinary status read shows. ... Every standing refusal names the evidence or the operator action that ends it."*

**Tension:**
There is a two-part tension between **[WT-2a.15]** and the diff:
1. **Operator action / terminal escape hatch vs. permanent hold:** The diff's updated specification (`03-Manager_Architecture.md`, `05-Message_Flow_and_State.md`, `07-System_Invariants.md`) states: *"When no resolving evidence ever arrives, the request stays held; no supported manual-release operation is introduced here."* While the spec names the *evidence* that ends the hold (a matching task-authored started snapshot or durable scoped process receipt), if the previous Manager crashed in the narrow window after pre-launch checkpointing and *before* the OS process was actually created, that evidence can never arrive in the world—leaving the reservation and its ancestry closure permanently held with no supported operator action to end it.
2. **Published refusal fact vs. derived in-memory hold:** In the code diff, holding a no-receipt launch moves the TID into `self._held_launch_feedback` and `self._family_scheduler._held_ancestors`, and moves dependent `PendingRequest` objects into `self._family_scheduler._held_pending`, while logging a runtime warning and retaining the underlying pre-launch checkpoint entry (with empty `host_processes`) in `weft.state.tasks.<manager_tid>`. No distinct refusal record naming the held descendant request IDs, the explicit refusal reason, and the ending condition is published as a dedicated queue fact by this diff; an ordinary status read can at best infer the condition indirectly from the retained Manager checkpoint's empty `host_processes` entry and the unconsumed pending/request rows.

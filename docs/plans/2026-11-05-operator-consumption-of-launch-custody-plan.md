# Operator Consumption Ends Pending Launch Custody

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6.2]; docs/specifications/07-System_Invariants.md [OBS.6], MANAGER.11
Superseded by: none

Class: 5. Strategy B: spec, code and tests change together. Baseline b7671da6.
Owner decision, 2026-11-05: keep default pending-message semantics for launch
custody. Operator queue read is an escape hatch, not ordinary automatic launch
settlement. The owner explicitly rejected include_claimed=True as over-armoring.
The launch-claim fence is withdrawn: no observed natural incident justifies its
complexity; deterministic fault injection shows possibility, not incidence.

## Scope and invariants

Use default reads for launch-reservation custody, ACK confirmation, orphan
reservation enumeration and spawn reserved-policy enumeration. Do not globally
remove claimed-row inspection from terminal evidence, STOP inventory or unrelated
readers. A read/physical delete of the exact reserved request ends its pending
custody. No-receipt reconciliation releases its derived family hold and removes
the resident/checkpoint reference, including recovery through older checkpoints.
This asserts no task execution, death, failure or completion and never requeues.
Normal pending ambiguous requests remain held. Failed reads change nothing.
Active launch workers and local children retain their existing protection;
receipted live residents remain supervised after custody handoff.

Theory: [WT-2a.7] explicit operator disposition over queue consumption;
[WT-2a.2] pending queue fact owns custody, not claimed physical residue;
[WT-2a.13] no death/terminal inference; [WT-2a.15] cleared causal obligation
releases its hold. No new queue, status, timer, recovery verb, broker operation,
sidecar, attempt model, or inferred process evidence.

## Exact spec delta

In 03 [MA-1.8], 05 [MF-5] and 07 [OBS.6], extend the retained no-receipt rule:

Addition:

```text
Launch custody follows ordinary pending-message semantics. A successful default
peek finding the exact reserved request absent, including after an operator
queue read or exact delete, ends that pending custody. Reconciliation retires a
no-receipt resident and releases its derived ancestry hold without replay or a
terminal/death inference. A retained checkpoint cannot recreate custody for a
request that is no longer pending. Pending ambiguous requests remain held;
failed reads preserve the obligation. Active launch workers/local children and
positive process supervision retain their existing protections.
```

Update nearby ACK/checkpoint-retention wording to say pending reservation and
pending absence, not physical deletion as the only possible release. [MF-6.2]
orphan/reserved-policy enumeration uses default pending visibility; no claimed
row is replayed. Add backlinks and owning docstrings in the same change.

## Slices and tests

1. RED in tests/core/test_admission_recovery.py using its real broker/checkpoint
   fixtures: parameterize operator read and exact delete; reconcile, then adopt
   an older retained checkpoint with a successor. Assert pending custody guard
   clears, no held resident/refusal/checkpoint reference remains, no replay or
   fabricated terminal outcome. Also retain the existing untouched-request test
   and live-receipt/snapshot tests. Inject read failure in the existing failure
   test rather than inventing another framework. Record actual RED assertion.
2. Update weft/core/task_state.py and narrow custody sites in Manager. Reuse
   retirement/release paths; no parallel state machine or new abstraction.
   Verify on SQLite and PostgreSQL at standard concurrency. Inspect every
   include_claimed occurrence; keep unrelated evidence/history inspection.
3. Withdraw fence plan and record explicit rationale. Add withdrawn to the
   closed metadata vocabulary/index/runbook and its corpus gate. Document the
   existing CLI escape hatch in README, including save-before-read, exact owner
   queue, immutable old-TID outcome remaining unknown, fresh-TID resubmission,
   and descendants returning to ordinary eligibility. No synthetic production
   incident or automatic retry promise. CLI flags must be checked in source.

## Verification, review and closure

Run admission recovery, task-state, Manager/family-maintenance and metadata/spec
hygiene checks, then the corresponding PostgreSQL slice; Ruff and mypy for
changed owners, doc paths and whitespace. Expand only for unresolved failures.
Independent cross-family completed-work review and [WT-7] two-factor accounts
cover the actual diff and original owner scope. Record findings/dispositions,
measure relevant existing black-box probes; do not claim a new full-suite run.
Keep the unrelated STOP case observational. Mark this plan/index completed only
after the work and checks, not merely after withdrawal of the other plan.

## Rollback and limitations

Reverting restores physical-residue protection and can resurrect holds for
operator-consumed rows until broker vacuum. No data migration or new persisted
schema exists. Operator consumption is deliberate disposition, not proof the
original process cannot still run. Replacement execution requires user judgment
about duplicate effects. Clearing a hold permits existing descendants to proceed
under ordinary rules; do not promise their parent succeeded or rewire lineage.

## Review and execution log

- Native implementation slice: OpenAI family, three owned runtime/test files;
  parent inspected the diff, RED assertions and actual result logs. No nested
  delegation. Documentation and metadata changes stayed with the parent.
- RED before runtime edits: two failures in the parameterized operator case.
  Read failed because manager_launch_custody_pending returned True after claim;
  delete failed because the no-receipt resident survived reconciliation.
- GREEN: 82 recovery/task-state/family-maintenance cases on SQLite and 82 on
  PostgreSQL (20.00 s). The final strengthened two cases separately pass both
  backends, including persisted refusal clear, queued descendant eligibility,
  and successor adoption of an older checkpoint. No terminal or replay appeared.
- Ruff/format and mypy: changed runtime/test owners pass (3 sources). Metadata
  plus spec hygiene: 6 checks pass; doc paths and whitespace pass. Remaining
  Manager include_claimed=True sites are STOP log/history inspection, outside
  launch custody. No new runtime abstraction or dependency was added.
- PostgreSQL wrapper rejected a pytest -k selection before test execution; the
  final rerun used the exact node path. No timeout/load/concurrency reduction.

### Independent completed-work review

Tier 1: Google family, `agy --mode plan --sandbox --model
gemini-4-argon-medium --output-format json --print-timeout 900s` with the brief
from a temporary file and stdin closed. Baseline b7671da6, actual runtime/spec,
README and metadata diff plus owner source excerpts and explicit accepted
operator semantics. Read-only/no-tool prompt; verification limited to supplied
excerpts, not independent commands or executed tests. Status SUCCESS, nonempty
response, no denied actions. Findings, verbatim:

```text
### Verdict: **PASS**

- **P1 Findings**: None.
- **P2 Findings**: None.

---
```

Disposition: no P1/P2 findings. The review's non-actionable plan/index coupling
note is satisfied by retaining both plan files and matching withdrawn/current
index rows. Parent separately verified the test outputs and paths. Later edits
only strengthened the regression assertions/formatting and synchronized nearby
spec prose; runtime decision semantics are unchanged from the reviewed diff.

### [WT-7] two-factor fit

The author account was written before the blind response existed. The Google
family's fresh independent invocation received only program theory and the
actual runtime/spec diff, no plan, review log or author account; no tools or
other context. Theory at baseline b7671da6; reviewed diff SHA-256:
`ace21aa0dfd7527e59795375a498d67e0f49a22e99b0a3b7f999afa0cc702981`.
The account is tied to that snapshot; subsequent formatting and strengthened
test assertions do not change its behavioural boundary. Repeat on a materially
changed contract. Both accounts are valid; theory fit is not a closure veto.

Author account, verbatim:

```text
1. Verdict: fits.

2. [WT-2a.2] and [WT-4.1]: the pending reserved row is custody; claimed physical residue is broker maintenance, not a second application obligation. [WT-2a.7]: operator read/delete explicitly dispositions that pending reservation without replay; untouched ambiguous requests remain held. [WT-2a.13]: no process death is inferred, and receipted live/local children stay supervised. [WT-2a.15]: removing the causal pending obligation releases held ancestry and its diagnostic. [WT-4.2]: ordinary existing queue primitives suffice; no new queue, timer, fence or broker API.

3. Refuses guessed execution/death ([WT-2a.13]), automatic replay ([WT-2a.7]), MonitorStore query truth ([ALT-WT-020]), another lifecycle queue table ([ALT-WT-019]) and fixed polling caps ([ALT-WT-002]). The queue operation is an operator escape hatch, not automatic liveness inference.

4. Falsifiable predictions: public read and exact delete clear the pending custody guard, checkpoint resident, ancestry hold and published refusal in the current Manager and a successor adopting an older checkpoint. Untouched pending requests remain held; failed reads change nothing. No terminal row or requeued request appears. Normal snapshot handoff, receipted-live supervision and active/local launch guards remain intact. Any replay, fabricated terminal result, resurrected hold or lost live supervision refutes the account.

5. Worst-fitting sentence: [WT-2a.7], “an unsettled reserved row persists independently of the process that took it until it is acknowledged or explicitly dispositioned”. A read claims before handing the payload to its caller, so leaving pending visibility does not prove receipt or processing. The owner explicitly chooses ordinary queue consumption as disposition. This must not be described as exactly-once manual processing or proof that original work never ran. That accepted boundary is the simplification.
```

Independent account, verbatim:

```text
### 1. Verdict
**Fits** (with minor boundary strain around SimpleBroker's internal claim vs. physical vacuum model).

---

### 2. Exact Theory Sections

* **[WT-2a.15] A standing refusal is a fact, and it is scoped**: *"Every standing refusal names the evidence or the operator action that ends it."*
  The prior spec explicitly forbade manual resolution of an ambiguous no-receipt launch (`"When no resolving evidence ever arrives, the request stays held; no supported manual-release operation is introduced here"`), which left a standing hold with no operator action to end it if the target process never published a receipt. This diff supplies that operator action via standard queue consumption (an operator queue read or exact delete of the reserved request).
* **[WT-2.2] Queues carry durable facts; processes carry ephemeral hints; consumed queues disappear** & **[WT-2a.2] Queues are the only durable truth** & **[WT-4.1] Consume-to-disappear queues, not relational tables**:
  By removing `include_claimed=True` from Manager and `task_state` reservation peeks, launch custody is governed by whether the reserved queue row is *pending* (unconsumed) rather than whether an already-claimed/consumed row still physically sits in the database awaiting SimpleBroker vacuum. Once consumed by the Manager or an operator, the row ceases to act as a durable custody fact, and `. _retire_scheduler_residents` can retire the in-memory no-receipt resident and its derived ancestry hold on the next reconciliation pass.
* **[WT-2a.7] Exactly once per reservation**:
  *"A work item executes exactly once per successful reservation path; an unsettled reserved row persists independently of the process that took it until it is acknowledged or explicitly dispositioned..."*
  When an operator explicitly consumes or deletes the reserved request of a retained no-receipt launch, reconciliation retires the custody reference and releases its derived ancestry hold *without* replaying the ambiguous request.
* **[WT-2a.8] SimpleBroker owns queue mechanics** & **[WT-4.2] Push a concern down when it belongs lower**:
  SimpleBroker owns delivery claims and physical vacuum timing. Weft's custody checks previously reached past SimpleBroker's default pending semantics by passing `include_claimed=True` to `peek_one` / `peek_generator`, making Weft sensitive to claimed-but-unvacuumed residue after a read. Relying on default `peek_one()` / `peek_generator()` delegates claim visibility back to SimpleBroker.
* **[WT-2a.13] Independent lifetimes; death needs proof** & **[WT-4.3] Availability and death are different questions**:
  Retiring a no-receipt resident after confirmed pending absence releases the scheduling hold and custody obligation *without* fabricating a terminal state or inferring process death (`"without replay or a terminal/death inference"`), and preserves active launch workers, local children, and positive process (`host_processes`) supervision.
* **[WT-4.4] Observable with standard tools** & **[WT-5] What Weft is not**:
  Rather than inventing a privileged Manager control command or side-channel to clear a stuck no-receipt hold, the escape hatch is an ordinary SimpleBroker queue read or delete on the reserved queue.

---

### 3. Refusal & `[ALT-WT-*]` Entries

* **Refuses unconsumed/claimed row retention as a tombstone/custody table — `[ALT-WT-019]` (Using SimpleBroker queues as unconsumed relational tables or event logs)**:
  Treating a claimed (already-read) row in `T{manager}.reserved` as still holding custody via `include_claimed=True` meant that an operator consuming the queue message via an ordinary broker read did not actually clear custody until physical vacuum or `delete_message_ids`. Dropping `include_claimed=True` ensures that consuming the message immediately ends its role as a live queue fact.
* **Refuses a second control plane or bespoke manual-release RPC — `[ALT-WT-010]` (A new scheduler, event bus, index, or cache subsystem) & `[WT-5]` (no second control plane)**:
  Instead of adding a dedicated Manager RPC or state-mutation command to break an ambiguous launch hold, the change uses existing queue primitives (ordinary read/delete on the reserved queue) as the operator action.
* **Refuses automatic retry/replay of an ambiguous launch — `[ALT-WT-006]` (Task-level REQUEUE as a reserved-queue policy)**:
  When a no-receipt launch reservation disappears via operator consumption, the Manager retires the resident and releases the ancestry hold without requeuing or replaying the request, preserving `[WT-2a.7]`.

---

### 4. Falsifiable Predictions

1. **Operator read (claim) immediately ends no-receipt hold before physical vacuum**:
   If a launch is stuck in a retained no-receipt state (pre-launch checkpoint written, no `host_processes` receipt, no task snapshot) and an operator performs a standard destructive queue read (which claims the message in SimpleBroker without necessarily running physical vacuum immediately) on `T{manager}.reserved`, the next Manager reconciliation pass (`_retire_scheduler_residents`) will observe `broker.peek_one(reserved_queue, exact_timestamp=request_id) is None`, remove the resident from `self._scheduler_residents`, release its `_family_scheduler` hold so dependent descendants can dispatch, and emit **neither** a replay of that TID **nor** a synthetic terminal event on `T{tid}.ctrl_out` / `weft.log.tasks`.
2. **Checkpoint retirement unblocks on claimed-but-unvacuumed reservations**:
   In `manager_launch_custody_pending`, once all referenced launch reservations in a dead Manager's checkpoint have been consumed (claimed via ordinary read or deleted), `LivenessMonitor` and `TaskMonitor` will evaluate `manager_launch_custody_pending(...) == False` and allow newest-checkpoint / reserved-queue retirement to proceed even if SimpleBroker has not yet physically vacuumed the claimed row.
3. **Live process evidence overrides pending absence**:
   If a resident in `self._scheduler_residents` has live `host_processes` (`self._scheduler_evidence_live(tid, evidence)` is `True`), or is in `self._active_child_launches` or `self._child_processes`, deleting or reading its row in `T{manager}.reserved` will **not** cause `_retire_scheduler_residents` to retire the resident early; the guard at the top of `_retire_scheduler_residents` continues to protect it until positive process exit.
4. **Potential falsifier / race if a claim is transient**:
   If any actor or tool ever performs a temporary/aborted claim on `T{manager}.reserved` that hides the row from default `peek_one()` (without `include_claimed=True`) and then releases the claim back to pending, a concurrent Manager reconciliation pass or `LivenessMonitor` check during that window would observe `peek_one() is None` and prematurely retire the no-receipt resident or the dead Manager's checkpoint. The correctness of this diff predicts that claims on `T{manager}.reserved` are strictly terminal consumption (never rolled back after being visible to another connection, or isolated by transaction boundaries).

---

### 5. Worst-Fitting Quoted Theory Sentence and Reasons

> **Quoted sentence (`[WT-4.1]`, "It is just a message"):**
> *"Protocols are ordinary messages on ordinary queues. Replies are pushed to a queue the requester's reactor already watches—push, never pull-scan."*
> *(Paired with `[WT-2.5]`: "Crash-safe launch handoff is two-phase reservation custody between `T{manager}.reserved` and `weft.state.tasks.<manager_tid>` until task-owned `weft.state.tasks.<tid>` evidence or whole-process settlement is observed.")*

**Reasons it fits worst:**
1. **Two-phase custody ended by unilateral out-of-band queue consumption:** `[WT-2.5]` states that two-phase reservation custody holds until *"task-owned `weft.state.tasks.<tid>` evidence or whole-process settlement is observed."* This diff introduces a third termination condition for custody—confirmed pending absence via an external operator read/delete on `T{manager}.reserved`—which is neither task-owned snapshot evidence nor whole-process settlement. While `[WT-2a.15]` demands an operator action to end a standing refusal, `[WT-2.5]`'s closed two-branch definition of custody settlement does not mention operator consumption.
2. **Pull-polling for operator consumption:** Because an external operator reading or deleting a row from `T{manager}.reserved` mutates the Manager's reserved queue directly rather than sending a control message to `T{manager}.ctrl_in`, the Manager learns that the operator ended the hold only by polling `broker.peek_one(evidence["reserved_queue"], exact_timestamp=request_id)` during periodic maintenance/reconciliation (`_retire_scheduler_residents`), and the specs note that *"reserved rows retained in launch/handoff custody do not count as pending reactor work or trigger `PEEK` wakeup activity."* Using direct queue surgery + maintenance polling as an operator protocol strains both `[WT-4.1]` (*"It is just a message... push, never pull-scan"*) and `[WT-2.6]` (*"Each Task owns its `inbox`, `reserved`, `outbox`..."*, where another process directly consumes from the Manager's `reserved` queue).
```

Comparison and classification:

| Agreement/divergence | Classification and disposition |
|---|---|
| Both find fit and predict read/delete release without replay or terminal inference, preserved live supervision and no checkpoint resurrection | Agreement; exercised by real owner-boundary probes on both backends |
| Author's worst fit is [WT-2a.7] claim-before-delivery; independent calls the operator escape hatch a third custody ending and pull-observation strain | Agent reasoning / accepted boundary, recorded rather than erased. [WT-2a.7] already permits explicit disposition; [WT-2] describes ordinary automatic handoff, not a ban on the owner's explicit queue escape hatch. Existing maintenance observes it; no new poller or task request protocol is introduced. No theory edit or extra guard is warranted |
| Independent predicts a transient visible claim followed by automatic return to pending could break the boundary | No such ordinary read semantic: materialized broker read commits consumption; delivery spec [SB-DELIVERY-1] says a claimed row is not automatically returned. The user-directed escape hatch accepts claim-before-handoff, not exactly-once operator processing. Explicit restoration is a new operator action, outside this procedure |

### Touched theory audit

Carry unchanged theory/code audit rows from the completed scoped-dispatch plan.
These rows are re-audited at the actual Manager/queue owner boundary; no new
CPU/latency or full-suite claim is made.

| Theory claim | Contract/evidence | Result |
|---|---|---|
| [WT-2a.2] queues own custody | Pending reserved row, default shared custody predicate; read/delete probes on SQLite and PostgreSQL | Aligned at owner boundary |
| [WT-2a.7] explicit disposition, no replay | Both operator probes leave original source absent, checkpoint reference removed; untouched pending retention regression remains green | Aligned at owner boundary |
| [WT-2a.13] no death/terminal inference | Operator probes produce no task snapshot/terminal envelope; existing receipted/live handoff cases remain green | Aligned at owner boundary |
| [WT-2a.15] scoped refusal ends on operator action | Both probes establish a published refusal over two due generations, clear it after consumption, expose the queued descendant, and repeat through successor recovery | Aligned; 2/2 cases per backend |

### Documentation decision

The original fence proposal is withdrawn, not completed. `withdrawn` is a new
closed metadata token explicitly authorized by the owner's request, matched by
index/runbook/corpus gate. It introduces no runtime policy. The README procedure
uses source-verified existing flags and records the old outcome as unknown;
read/delete clears the pending hold, and replacement work gets a new TID.

### Final verification and closure

- Additional complete Manager file: SQLite 302 passed / 3 PostgreSQL-only skips;
  PostgreSQL 296 passed / 12 SQLite-only skips (68.65 s). Exit codes zero.
- Parent reran Ruff/format on all four touched Python files, and metadata/spec
  hygiene plus doc-path and whitespace checks. No full repository suite is
  claimed. Existing default concurrency was retained on both backends.
- This narrow custody correction is completed; the fence stays withdrawn.
  No changes were committed or pushed as part of this request.

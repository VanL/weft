# Launch-Claim Fence: Settle Ambiguous Launches With Positive Evidence

Status: withdrawn
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1], [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6.2]; docs/specifications/07-System_Invariants.md [OBS.6], MANAGER.11; docs/specifications/00-Quick_Reference.md
Superseded by: none

## Withdrawal decision — 2026-11-05

Withdrawn by the owner, not implemented. No naturally occurring instance of
this launch ambiguity has been observed in the evidence discussed here. The
recorded deliberate Manager-kill probe establishes that the window exists;
it does not establish an operational incidence or cost sufficient to justify
the fence, new queues and settlement/cleanup protocol.

Use the existing queue escape hatch and documented manual recovery instead;
see README's “Recovering an ambiguous Manager launch” section and
`2026-11-05-operator-consumption-of-launch-custody-plan.md` for the narrowly
scoped pending-custody correction. There is no approved fence implementation
or broker API dependency. Reconsider only on observed incidents demonstrating
that operator recovery is inadequate, with evidence of frequency and impact.

The original proposal and review history below are retained for rationale,
not executable task instructions. Its prior owner naming/disposition decisions
do not authorize implementation after this withdrawal.

Class: 5, risky. It changes the child launch protocol on the durable spine
(Manager -> launcher -> task process), adds one Manager-owned queue, and adds a
settlement outcome at a custody boundary. Hardening and Tier-1 cross-family
review apply. Promotion strategy B (spec, code and tests land atomically). This
plan authorizes no implementation; the owner asked for a plan.

## 1. Goal

A launch is ambiguous when the Manager durably wrote its pre-launch checkpoint
but no post-launch process receipt exists. Today that state cannot be
resolved: "never started" and "started, then died before publishing" look the
same. D1 (`2026-11-05-scoped-ambiguous-launch-dispatch-plan.md`) limits the
damage to the held request and its ancestry closure, but the request stays
held forever: "When no resolving evidence ever arrives, the request stays
held; no supported manual-release operation is introduced here."

This plan makes the system produce the missing evidence.

- Before starting the process, the launching Manager writes one claim row to
  its own claim queue and records the row's exact message ID in the
  pre-launch checkpoint.
- Before constructing its task, the child must delete that exact row.
- A Manager holding the ambiguous launch settles it by trying to move the
  same exact row into the child's settlement queue `T{tid}.never_started`.
  SimpleBroker's exact delete and exact move are each atomic on one row, so
  exactly one side gets it.
  - **The move succeeds:** the task never ran and now cannot. The moved row
    is a durable, resumable record of that win. The Manager commits a
    terminal envelope (`failed`, error `launch_never_started`), which then
    serves as the permanent proof. It acknowledges the reservation, and only
    then deletes the record.
  - **The row is already gone:** the child took it. That is started evidence
    for scheduling; custody rules continue unchanged.
  - **The read or move fails:** nothing changes.

This is the classical fencing pattern: a one-shot token whose consumption
decides ownership. Because the trace survives until disposition completes, the
pattern also stays correct across crashes (see §4).

The design answers the owner decision D1 and D5 left open, without replay and
without an operator release verb. The user reruns the work with a new TID.

## 2. Source Documents and Causal Premise

- `docs/program-theory.md`:
  - [WT-2a.7] Exactly once per reservation; crash residue awaits explicit
    recovery.
  - [WT-2a.8] SimpleBroker owns atomic moves.
  - [WT-2a.9] One current contract.
  - [WT-2a.13] Death needs proof.
  - [WT-2a.15] A standing refusal names the evidence that ends it.
  - [WT-4]:
    - "Consume-to-disappear queues";
    - "Measure; do not bake in inefficiency", which requires broker-operation
      counts on both backends.
  - [ALT-WT-012].
- Specs:
  - `03-Manager_Architecture.md` [MA-1] items 2–3 and [MA-1.8]: the retained
    pre-launch paragraph.
  - `05-Message_Flow_and_State.md`:
    - [MF-5]: the same paragraph; its acknowledgement sentence naming
      "positive launch-failure/whole-process settlement evidence"; and its
      sentence "No new queue or reserved-message format is introduced";
    - [MF-6.2].
  - `07-System_Invariants.md` [OBS.6], MANAGER.11.
  - `00-Quick_Reference.md` queue table.
- Plans:
  - D1 and the wrapper-lost idempotence repair
    (`2026-11-05-wrapper-lost-settlement-idempotence-plan.md`) are
    constraints, not scope.
  - D5 `2026-11-05-manager-published-refusals-plan.md` lands **before** this
    plan and is a constraint, not scope. Its owner has assigned it for
    implementation. This plan relies on three parts of D5:
    - the `launch_receipt_missing` reason and its `matching_started_evidence`
      ending;
    - the standing-refusal rule: a refusal is published only after it
      persists beyond one due maintenance pass;
    - the rule that a normal per-task read failure changes no diagnostic.

    This plan amends only D5's definition of matching started evidence (§8).
- Guidance:
  - `AGENTS.md` and the `docs/agent-context/` read order;
  - `docs/agent-context/runbooks/hardening-plans.md`;
  - `docs/agent-context/runbooks/testing-patterns.md`;
  - `skills/call-agent/SKILL.md`, `skills/test-audit/SKILL.md`.

Causal premise, verified by probe on the D1 worktree:

- The launching Manager was SIGKILLed immediately before `Process.start`.
- A successor dispatched unrelated work in about 2 s.
- The held task ran 0 times, across two successors.
- `weft task status` reported it `reserved` indefinitely.

Why the existing inbox seed cannot be the token (verified in source):

1. **Not universal.** `_seed_child_inbox` runs only when `inbox_message` is
   set. Persistent tasks without input, pipeline launches
   (`seed_start_envelope=False` in `weft/commands/run.py` and
   `weft/core/tasks/pipeline.py`) and the heartbeat (`"inbox_message": None`)
   get none.
2. **Not one-way.** `Consumer._handle_work_message` and
   `_requeue_reserved_message` move a reserved row back to the inbox when
   the task is paused or busy.
3. **Read as work.** A stale seed is consumed as input.
4. **Its ID is discarded.** `_seed_child_inbox` drops the ID `write` returns.

## 3. Current Structure and Placement

Launch path today:

1. `Manager._launch_child_task` seeds the inbox (if any).
2. `_checkpoint_admission_launch` persists the pre-launch checkpoint
   (MANAGER.11).
3. The broker-free worker calls `weft/core/launcher.py::launch_task_process`
   (single caller), which spawns `_task_process_entry` with data-only args.
4. The child constructs `task_cls(...)`;
   `BaseTask._initialize_base_task_runtime` publishes the first task-state
   snapshot. That publication is best-effort (`_register_tid_state` returns
   False on broker failure and execution continues).

Recovery path today:

- `_recover_scheduler_paths` and the lost-worker-result path place every
  no-receipt resident under `_hold_ambiguous_launch`.
- `_advance_launch_custody` ends the hold on started evidence.
- `_retire_scheduler_residents` commits terminal envelopes through the
  idempotent `_commit_wrapper_lost_envelope` before `_ack_launch_custody`.

Placement:

- **Queues.** Two constants go in `weft/_constants.py`; naming is owner
  decision F2.
  - `QUEUE_UNSTARTED_SUFFIX` for `T{manager_tid}.unstarted`: open
    claims, written only by the launching Manager.
  - `QUEUE_NEVER_STARTED_SUFFIX` for `T{tid}.never_started`: one
    in-progress settlement record per settled child, written only by a
    settling Manager's exact move.

  Neither suffix is in `STANDARD_TASK_QUEUE_SUFFIXES`, and
  `standard_task_queue_identity` rejects both. TaskMonitor discovery
  therefore never deletes them, and no TaskMonitor change is needed. Every
  row has a Manager-owned deleter (§4, retirement rule).

  Why two queues:
  - The unstarted queue is keyed by Manager, so a successor can retire orphans
    of a dead Manager by paging one queue.
  - The settled queue is keyed by child TID, so any successor finds an
    in-progress settlement from the TID alone, without knowing which Manager
    settled it.
  - The move's source and target always differ. SimpleBroker rejects a move
    whose source and target are the same queue.
- **Manager writes:** in `_launch_child_task`, after the seed and before
  `_checkpoint_admission_launch`.
- **Child claim:** in `launcher.py::_task_process_entry`, before
  `task_cls(...)` and therefore before any publication. Tasks constructed
  outside the launcher carry no claim reference, which is the ordinary
  direct-construction entry path.
- **Settlement:** one `Manager` method, run on the reactor and called from
  the held-resident rotation in `_refresh_scheduler_evidence`. With F3 it is
  also called from the lost-result hold.

### Read first and comprehension questions

Read:

- `Manager._launch_child_task`, `_checkpoint_admission_launch`;
- `_adopt_scheduler_evidence` (the identity-key check compares
  `previous[key]` with `evidence.get(key)`);
- `_hold_ambiguous_launch`, `_recover_scheduler_paths`,
  `_advance_launch_custody`, `_ack_launch_custody`,
  `_retire_scheduler_residents`, `_commit_wrapper_lost_envelope`,
  `_terminal_envelope_visible`;
- `_maintain_orphan_reservations`, `_recover_unlaunched_reservations`;
- `launcher.py::_task_process_entry`;
- `BaseTask._initialize_base_task_runtime`, `_register_tid_state`;
- `weft/core/monitor/policies/dead_task.py::standard_task_queue_identity`;
- SimpleBroker `delete_message_ids` (count) and
  `move_one(exact_timestamp=...)` (row or None; ID preserved) on both
  backends. Each is one transaction; there is no multi-queue transaction.

Write answers in the execution log before the first edit. A wrong answer
means stop and re-read.

1. Why does the settling Manager move the claim rather than delete it?
   - *Expected:* disposition takes several separately committed steps. A
     delete leaves no trace, so after a crash or failure the next attempt
     sees an absent row and misreads its own win as a child win. The moved
     row is the durable trace until disposition completes.
2. Why must the child claim before `task_cls(...)`?
   - *Expected:* a losing child must publish nothing. Otherwise a stale state
     row could be appended for a TID the Manager has settled as
     `launch_never_started`.
3. What does an absent row prove?
   - *Expected:* only that the child took the claim. It ends the D1 scheduling
     hold. It does not prove death, authorize ACK, or prove that no user code
     ran: first-snapshot publication is best-effort.

## 4. Contract and Invariants

### Protocol

**Launching Manager, per launch with a pre-launch checkpoint:**

1. Write one claim row to `T{self}.unstarted` and keep the returned ID.
   - The body is JSON naming the child TID, for operators only. Nothing
     reads it as authority.
   - A failed write aborts the launch through the existing seed-failure path.
2. Record `launch_claim: {queue, message_id}` and `terminal_route` (the
   configured `ctrl_out` name the launch would use) in the checkpoint
   evidence. Settlement needs the route after ACK has deleted the reserved
   request body.
   - It is Manager-owned attempt evidence, **not** a task identity key.
   - It is not added to `_adopt_scheduler_evidence`'s immutable identity
     check: child snapshots never carry it, and that check raises when a key
     present in `previous` is absent from the new evidence.
   - The merge keeps the checkpoint value when a snapshot omits it. Two
     checkpoints naming *different* claims for one TID cannot arise under
     this plan (no same-TID relaunch from a settled claim). If they do,
     treat it as the existing "scheduling identity changed" error.
3. Pass the claim reference to the child as one new data-only argument of
   `_task_process_entry`.

**Child, in `_task_process_entry`, when a claim reference is present:**

- Call `delete_message_ids(queue, [message_id])` once.
- Count 1: continue as today.
- Count 0, or a broker error: exit immediately with a named constant exit
  code. Construct nothing and publish no state, outbox, ctrl_out or log row.
- One attempt; no retry and no fallback.

**Settling Manager.** It may act only on a held resident for which all of the
following hold:

- its checkpoint evidence has `launch_claim`;
- it has no `host_processes`;
- it is not in `_active_child_launches`;
- a successful read of `weft.state.tasks.<tid>` found no task-owned snapshot.

No bootstrap gate is needed. Settlement may run in the bootstrap evidence
pass, because the settlement record is found from the child TID alone.

Each due pass classifies the claim with at most three exact reads, in this
order. Any failed read changes nothing, and the next due pass repeats it:

1. **Record present** — the claim's message ID is in `T{tid}.never_started`:
   a settlement is in progress, so resume the disposition below.
2. **Open claim present** — the row is still in `launch_claim.queue`.
   Attempt
   `move_one(launch_claim.queue, f"T{tid}.never_started", exact_timestamp=message_id)`:
   - row returned: this Manager won; run the disposition below;
   - None: the child took the row between the reads; continue to step 3.
3. **Neither present.** Read the recorded `terminal_route` for a
   Manager-source envelope with error `LAUNCH_NEVER_STARTED_ERROR` matching
   the TID, using `_terminal_envelope_visible`'s generalized recognition:
   - found: a settlement already completed; finish idempotently with steps
     2–4 below;
   - not found: the child took the claim, which is started evidence. End the
     D1 scheduling hold through `_advance_launch_custody`'s release. Keep the
     reservation and checkpoint until snapshot handoff or positive
     settlement, and remember this owner-locally so the reads are not repeated
     each pass.

Disposition. Steps run in order; each is idempotent:

1. Commit the Manager-source terminal envelope (`status: failed`, `error:
   LAUNCH_NEVER_STARTED_ERROR`, a new constant) on `terminal_route`. Reuse
   `_commit_wrapper_lost_envelope`'s writer, generalized by error constant.
   Extend `_terminal_envelope_visible`'s Manager-source recognition to this
   error, so a repeat call writes nothing. From this point the envelope is
   the permanent proof of settlement.
2. `_ack_launch_custody` (exact ACK; an already absent reservation is
   success). This is the "positive launch-failure settlement evidence"
   [MF-5] already names.
3. Exact-delete the record from `T{tid}.never_started` (count 0 is success).
4. Retire the resident (`FamilyScheduler.retire` / `release`) and publish the
   checkpoint.

A failed step leaves everything after it undone, and the next due pass
resumes. Crash coverage:

- a crash after the move and before step 1 leaves the record, which
  classification step 1 finds;
- a crash after step 1 leaves the envelope, which classification step 3 finds
  once the record is gone;
- a successor that re-adopts an older retained checkpoint naming the
  resident reaches classification step 3 and retires the resident.

At no point does "claim absent" alone become started evidence while a
settlement is in progress or completed.

Why absence may count here at all ([WT-4.3] treats absence as `unknown`): the
row is a recorded fence with an enumerated set of deleters (the retirement
table below). None of them deletes the open claim of a still-held resident
except the child itself. An operator `weft queue delete` would violate that
and be misread as started; the effect is the safe direction (eligibility
released, custody retained, no replay).

### Why fail-closed rather than requeue (owner decision F1, decided)

- **Requeue reuses the TID.** Requeue relaunches the same TID with a new
  claim. Older checkpoints that name the first attempt stay retained for at
  least 2,400 s (07 retirement age). A later successor then merges two
  attempts for one TID. That needs a per-attempt identity model the
  scheduler does not have.
- **Fail-closed reuses existing machinery.** It ends every attempt in
  positive terminal evidence and an exact ACK, and needs no seed deletion,
  no relaunch and no attempt key.
- **It matches the owner's position.** The owner rejected replay ("too much
  magic"). Fail-closed reports a definite outcome; rerunning the work is the
  user's decision, under a new TID.

The owner chose fail-closed on 2026-11-05. Requeue is recorded as the
rejected alternative; choosing it later would require a separate same-TID
attempt-identity plan. Automatic recovery, if wanted, belongs to a future
`weft task retry TID` (new TID), not to this protocol.

### Invariants

- [WT-2a.7] / MANAGER.11:
  - a request never re-executes through this path;
  - ACK follows positive terminal evidence;
  - an absent claim never authorizes ACK or terminal inference.
- [WT-2a.13]: a won claim proves non-execution, so the task is settled as
  never started, not as dead. An absent claim proves started, not alive.
- A task-owned terminal envelope still outranks a Manager envelope. A losing
  child publishes nothing, so it can never contradict a settled outcome.
- No settlement while a receipt exists or a launch worker is active. A
  receipted wrapper that dies before claiming stays on the ordinary
  `wrapper_lost` path.
- No new timer or wake. Settlement runs in the existing held-resident
  rotation, whose deadline is already armed while residents exist
  (`_admission_retry_timeouts`).
- No TaskSpec or `io` change and no new dependency. Additions: one queue
  suffix, two evidence keys and one error constant. CHANGELOG notes them,
  with no compatibility shim.

### Retirement rule for claim and settlement rows

Every row has a named Manager deleter:

| Row | Deleted by |
|---|---|
| Open claim, child wins | the child |
| Open claim, Manager wins | moved into `T{tid}.never_started` |
| Settlement record | the settler, at disposition step 3 (or any resuming successor) |
| Open claim of a resident settled any other way (snapshot handoff, receipted `wrapper_lost`, launch failure) | the Manager that ACKs or retires the resident: one exact delete in `_ack_launch_custody` / retirement, with count 0 meaning success. No process can still claim it: either the task already claimed it, or the process has positively exited or never existed |
| Claim of an aborted launch | the launching Manager, best-effort, on the abort path |
| Orphan in a dead Manager's unstarted queue (crash or failed delete; no retained checkpoint references it) | the successor, in `_maintain_orphan_reservations`'s bounded page over a positively dead Manager's `unstarted` |

An unreferenced orphan is safe to delete: either no process was ever created
for it, or its resident was already ACKed. A failed delete in a live Manager
leaves the row for that same rule after the Manager dies. Both queues empty and
disappear ([WT-4] consume-to-disappear).

### Hidden couplings

- `_ack_launch_custody` must drop `launch_claim` and `terminal_route`, and
  must exact-delete any still-open claim row (retirement rule).
- `_manager_checkpoint_residents` and `manager_launch_custody_pending` must
  carry and tolerate both keys.
- `weft task status` and `weft result` read the Manager envelope. Verify
  they render `failed` with `launch_never_started`, and exit codes follow
  the existing failed-task mapping.
- [WT-4] measurement: one extra Manager write and one child exact delete per
  launch. Measure median launch latency and broker-operation counts over 200
  launches on SQLite and PostgreSQL against D5's landed baseline. Stop for an
  owner decision if the median rises by more than 10%.

### Interaction with D5 (landed first)

- **Won claim:**
  - The resident retires with terminal evidence, so the
    `launch_receipt_missing` obligation no longer exists. D5 clears it
    through its ordinary resolved path, with no new `ends_when` value.
  - Settlement is called from `_recover_scheduler_paths`, inside the
    bootstrap evidence pass, which is D5's establishing pass. A fenced launch
    that settles there never becomes standing and forces no diagnostic
    append.
  - Verify the call order in code before relying on it. `_admission_bootstrapped`
    becomes true only after that loop, so do not gate settlement on it.
    Probe (h) checks the prediction.
- **Claim taken by the child** (classification step 3 found no
  `launch_never_started` envelope):
  - It is started evidence for eligibility. D5's `matching_started_evidence`
    names only post-launch process evidence, and an absent claim carries no
    PID. The §8 D5 blocks widen that definition.
  - The scheduling hold ends, so D5 publishes no dispatch refusal for it.
    Its reservation stays as a retained custody obligation, which D5
    excludes from dispatch reasons.
- **Failed claim read or move:**
  - Settlement changes nothing, and the hold persists.
  - If the hold survives into D5's next generation, D5 publishes
    `launch_receipt_missing` under its own standing rule. It clears the item
    after the next successful settlement.
  - The diagnostic follows the hold; settlement adds no diagnostic of its
    own.
- **No claim reference** (launches checkpointed before this change): no
  settlement evidence exists, so D1's existing rule holds them and D5 keeps
  publishing `launch_receipt_missing`. This adds no legacy reader; see §5 F4.
- **Code coordination:** do not start code while D5 is uncommitted. Both
  edit the same Manager owners. Rebase onto D5's commit, then re-verify every
  symbol named here.

### Residual ambiguity (stated, not hidden)

A claim taken by the child, with no snapshot and no receipt (the child won,
then died, or its first snapshot write failed), can hide executed user work: snapshot
publication is best-effort. Its custody stays retained and is never replayed.
This plan gives that state no new settlement.

## 5. Owner Decisions

- **F1 — disposition on a won claim. Decided (owner, 2026-11-05): fail-closed.**
  - The Manager settles the task as `failed` / `launch_never_started` and
    ACKs the reservation (see §4).
  - Requeue is rejected for this plan: it needs same-TID attempt identity.
  - Owner note: a `weft task retry TID` verb may be supported at some point.
    It would resubmit a settled task as a new attempt under a new TID, with
    provenance. `launch_never_started` would be its simplest case, since the
    original provably never ran. It needs its own plan and is not part of
    this one.
- **F2 — queue names.**
  - `T{tid}.never_started` for the settlement record (owner, 2026-11-05).
  - `T{manager_tid}.unstarted` for open launch claims (owner, 2026-11-05).
- **F3 — also settle a live Manager's own lost-worker-result case.
  Decided (owner, 2026-11-05): yes.** Same conditions, same evidence.
- **F4 — launches checkpointed before this change. Decided (owner, 2026-11-05):
  the recommended treatment below.**
  - *Recommended:* no dedicated code path. "No claim reference" is simply
    "no settlement evidence" under D1's current rule, which is not a second
    contract.
  - The CHANGELOG states that launches ambiguous at upgrade time stay held.
  - This keeps [WT-2a.9] / [ALT-WT-012] intact. The reviewer's
    RECONSIDERATION REQUESTED is recorded in the Review Log for the owner.

## 6. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## 7. Spec Baseline

- Drafted against `963dc83e` plus the D1 and wrapper-lost idempotence deltas
  recorded in their completed plans, and against D5's draft spec delta.
- Implementation baseline: the commit that lands D5, which includes D1 and
  the idempotence repair. Record that SHA here before the spec-promotion
  slice. Rebase every Replacement block onto it, and re-quote D5's landed
  wording in the D5 blocks.
- Plan type: implementation with spec revision, strategy B. Promotion
  baseline identifier: pending.

## 8. Proposed Spec Delta

Promotion strategy B for every block.

### [MA-1] item 3 — replace "3. **Initial payload delivery** – If the spawn payload includes `inbox_message`, the manager writes it into the child’s inbox queue before the child starts processing."

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
3. **Initial payload delivery and launch claim** – If the spawn payload includes
   `inbox_message`, the manager writes it into the child’s inbox queue before the
   child starts processing. Before the pre-launch checkpoint, the manager writes
   one claim row to its own `T{manager_tid}.unstarted` queue and records the
   row's exact message ID and the launch's terminal route in that checkpoint.
   The launched process deletes that
   exact row before it constructs its task or publishes any task-state, outbox,
   `ctrl_out` or log row; if the deletion removes no row or fails, the process
   exits without publishing anything. A task constructed without a claim
   reference behaves as before.
```

### [MA-1.8], [MF-5], [OBS.6] — in each file replace the identical span beginning "If a retained pre-launch reference has no durable post-launch process receipt" and ending "Such held work is outside the conditional drain guarantee."

Replacement (`docs/specifications/03-Manager_Architecture.md`, `docs/specifications/05-Message_Flow_and_State.md`, `docs/specifications/07-System_Invariants.md`):

```text
If a retained pre-launch reference has no durable post-launch process receipt, a successor preserves its validated identity, unresolved reservation, and finite frontier where applicable. The ambiguous launch and accepted requests whose immutable ancestry includes its TID remain held; unrelated roots and sibling branches may dispatch under the normal measured admission and frontier rules. When the retained reference names a launch claim, the Manager holding the resident settles the ambiguity while no launch worker for it is active and after a successful read of its task-state queue found no task-owned snapshot. It attempts one exact move of the open claim row into the task's `T{tid}.never_started` queue. A successful move proves the task never ran and cannot run. The moved row is the settlement record: the Manager commits a Manager-source failed terminal envelope with error launch_never_started on the launch's recorded terminal route, acknowledges the exact reservation, deletes the record, and retires the resident. Every step is idempotent, and a successor finding the record or that envelope completes the remaining steps. A claim row absent from both its unstarted queue and the never-started queue, with no launch_never_started envelope on the recorded route, means the launched task took the claim. That inference holds because the checkpoint records that the row was written, and only the launched task removes the open claim of a resident that is still held; the settling move and the Manager deletions of claims belonging to acknowledged or unreferenced residents cannot produce that state. It is started evidence that ends the scheduling hold but not custody: the reservation remains until task-owned snapshot handoff or positive settlement, and the request is never replayed. It does not prove that user work ran, or that the process is alive or dead. A failed claim read, move or envelope read changes nothing. A validated task-authored snapshot matching the immutable runtime identity and reporting a started process, or a durable post-launch process receipt, also ends the scheduling hold. A snapshot with an unknown process creation time may establish started evidence for eligibility but does not authorize custody acknowledgement or prove process death; missing evidence never authorizes replay or terminal inference. A failed required checkpoint publication remains a separate ordinary-dispatch barrier until durable custody is established. A fresh in-flight launch retains its startup-feedback gate, but a retained ambiguous launch does not occupy that transient observation window indefinitely. A retained reference without a launch claim has no settlement evidence: it stays held, and no supported manual-release operation is introduced here. Such held work is outside the conditional drain guarantee.
```

### [MF-5] — replace the sentence "No new queue or reserved-message format is introduced."

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
No reserved-message format changes. The added queues are each Manager's `T{manager_tid}.unstarted` and each settled task's `T{tid}.never_started` under [MA-1] item 3 and the paragraph above: open claims are consumed by the launched task, moved into the settlement queue by a settling Manager, or deleted by the Manager that acknowledges or retires their resident; settlement records are deleted when settlement completes; and a successor deletes rows of a positively dead Manager's unstarted queue that no retained checkpoint references.
```

### Quick Reference — insert after the `T{tid}.ctrl_out` row

Replacement (`docs/specifications/00-Quick_Reference.md`):

```text
| `T{tid}.unstarted` | Manager TIDs only: open one-shot launch fences; never task input |
| `T{tid}.never_started` | In-progress settlement record of a launch proven never started; never task input |
```

### D5 descriptors — after D5 lands (re-quote D5's landed text)

In D5's [MA-1.8] paragraph replace "D1's post-launch started evidence may end an eligibility hold without satisfying scoped custody handoff or death proof." with

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
D1's post-launch started evidence, or a launch claim found consumed under [MF-5], may end an eligibility hold without satisfying scoped custody handoff or death proof; a launch claim won by the settling Manager ends the refusal through launch_never_started settlement.
```

In D5's [MF-5] paragraph replace "Matching started evidence can release D1 eligibility despite unknown process creation time; scoped handoff and positive-death rules remain stricter." with

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
Matching started evidence, including a consumed launch claim, can release D1 eligibility despite unknown process creation time or an absent process receipt; scoped handoff and positive-death rules remain stricter.
```

Add Related Plans backlinks in 03, 05 and 07. Update the [MA-1.2], [MA-1.3]
and [MA-1.8] implementation mappings and the touched docstrings in the same
change.

## 9. Tasks

0. **Precondition: D5 has landed.** Confirm D5's plan and index row are
   `completed` and its commit is in `git log`. Record the SHA in §7 and
   re-quote D5's text in §8. Stop if D5 is in flight.
1. **Review and fit before code.**
   - Run a scoped re-review of this revision.
   - Pre-code [WT-7] fit test: done (Review Log). Repeat it only if §8
     changes materially again before code.
   - Record rulings F1–F4. Stop if any path can ACK, replay or settle on an
     absent claim.
2. **RED proofs on the real path.** Use the real broker and real processes,
   via `WeftTestHarness` or the fixtures in
   `tests/core/test_admission_recovery.py`. Fault only process timing or one
   broker call. A narrow test adapter at `_task_process_entry` may hold the
   child at two points: before the claim, and after the claim but before
   construction. It follows the existing kill-adapter pattern.
   - (a) Kill the launching Manager before `Process.start`. The successor
     settles: one `launch_never_started` envelope, the reservation ACKed,
     the target never runs, and `weft task status` shows `failed`. RED today:
     `reserved` forever.
   - (b) Hold the child *after* claiming, before construction, and kill the
     Manager. The successor sees an absent claim, so the hold is released, no
     envelope is written and the reservation is retained.
     - Sensitivity: treating "move returned None" as a win must fail this
       case.
   - (c) Hold the child *before* claiming and let the successor win, then
     release the child. Assert:
     - the child's exit code is the named constant;
     - zero task-state, outbox, ctrl_out and log rows for that TID besides
       the Manager envelope;
     - the target is never invoked;
     - producer closure, so the absence check is not vacuous: the same
       adapter, unheld, produces those rows.
     - Sensitivity: removing the child claim must fail this case.
   - (d) Interrupt settlement after the move, after the envelope, after the
     ACK and after the record delete, each with the settler killed. Include
     one settler killed during the bootstrap evidence pass, and one successor
     that re-adopts an older retained checkpoint after settlement finished. The next successor resumes and
     converges on exactly one envelope, one ACK and no trace row.
     - Sensitivity: replacing the move with a delete must fail this case.
   - (e) A receipted wrapper killed before claiming:
     - one `wrapper_lost` record, and no settlement attempt;
     - its open claim row is deleted when the resident is ACKed.
   - (e2) F3: a live Manager whose launch-worker result is lost settles its
     own launch through the same move (distinct source and target queues).
   - (f) An injected fault on the claim move: nothing changes, and the next
     pass settles.
   - (g) An orphan claim in a dead Manager's queue, with no referencing
     checkpoint, is deleted by the successor. A referenced row is never
     deleted.
   - (h) D5 interaction, read through the Manager task-state snapshot and
     `weft status --json`:
     - (a) yields no `launch_receipt_missing` item and no diagnostic-forced
       append;
     - (b) yields no dispatch refusal, while custody is retained;
     - a pre-change checkpoint without a claim still publishes
       `launch_receipt_missing`;
     - (f) with the fault persisting into D5's next generation publishes
       `launch_receipt_missing` once; that item clears after the fault is
       lifted and settlement completes.
3. **Manager write and checkpoint key.**
   - Files: `weft/core/manager.py`, `weft/_constants.py`.
   - Stop if the claim must enter TaskSpec `io` or the identity check.
4. **Child claim.**
   - File: `weft/core/launcher.py`.
   - Stop if it needs a retry loop or a `BaseTask` hook.
5. **Settlement and resumption.**
   - Generalize the terminal-envelope writer by error constant, keeping its
     idempotence and task-precedence rules.
   - Add orphan-row retirement to `_maintain_orphan_reservations`.
6. **Promote, measure, close.**
   - Apply §8 and update mappings, docstrings and the CHANGELOG.
   - Run the gates and record the measurements.
   - Run completed-work cross-family review and a fresh fit account.
   - Refresh the [WT-2a.7], [WT-2a.13] and [WT-2a.15] audit rows by running
     (a)–(d).
   - Flip the plan and index row to completed.

## 10. Testing Plan

- Use SQLite and PostgreSQL (`bin/pytest-pg`) at normal configured
  concurrency, with real spawned processes and real successors. Do not
  mock the broker, the scheduler or the process lifecycle.
- Each case asserts:
  - the target-invocation count, through a file the target writes;
  - the exact reservation and checkpoint state;
  - the contents of both claim queues;
  - the ordered ctrl_out and task-state rows.
- Sensitivity controls are listed in (b), (c) and (d). Record their actual
  RED output.
- Measurement is per §4.
- Windows: the launcher entry is portable. Use the existing narrow kill
  adapter for POSIX kill mechanics.

## 11. Verification and Gates

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_admission_recovery.py tests/core/test_manager.py tests/core/test_manager_family_maintenance.py tests/system/test_family_matrix.py -q
./bin/pytest-pg tests/core/test_admission_recovery.py tests/core/test_manager.py tests/core/test_manager_family_maintenance.py tests/system/test_family_matrix.py
./.venv/bin/python -m pytest tests
./.venv/bin/python -m pytest -m "" tests
./bin/pytest-pg --all tests
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
bin/check-doc-paths
bin/check-dom15-fixtures
git diff --check
```

Every `weft run` in a probe uses `</dev/null`.

## 12. Rollout and Rollback

- One-way door: the queue name, the two evidence keys and the
  `launch_never_started` error become persisted protocol.
- Rollback is a code revert. Old code ignores the new keys and holds such
  launches under D1, which is safe: there is no replay.
- Leftover rows in a dead Manager's claim queue would then remain until
  re-upgrade.
- No migration and no shim. A Manager always launches children from its own
  installed code.

## 13. Out of Scope

- Implementing D5 or changing its inventory, fields, standing rule or
  publication path (beyond the §8 wording).
- Requeue of won claims, and same-TID attempt identity.
- Operator `retry` or `delete` verbs. `weft task retry TID` may be supported
  later under its own plan (owner note, F1).
- Changes to `wrapper_lost` for receipted launches.
- New settlement for the §4 residual state.
- Pre-existing duplicate-seed residue from a crash before the checkpoint
  (observed while reading the code, not probed).

## 14. Fresh-Eyes Review

Author pass after the initial draft:

- Moved the child claim ahead of the first snapshot.
- Rejected the inbox seed as the token.
- Restricted settlement to no-receipt, no-active-worker residents.

Author pass after the review revision:

- The claim moved to a Manager-owned queue, which removed the TaskMonitor
  dependency entirely.
- Fail-closed disposition removed seed deletion and the same-TID relaunch.
- Settlement is a move with a durable trace, so every step after the win is
  idempotent and resumable.

Author pass after round 2:

- The record lookup is now one exact read keyed by TID.
- The envelope closes the post-delete window.

Remaining risk: classification makes up to three exact reads per held
resident per due pass. That cost is bounded by the number of held residents,
but measure it under probe (d).

## Review Log

### Round 1 — Tier-1 cross-family

- Author family: Anthropic (Claude). Reviewer family: OpenAI.
- Invocation: `codex exec -s read-only -C <repo> -c 'model_reasoning_effort="high"'`
  (codex-cli 0.161.0), using the runbook prompt.
- Reviewer read the plan and §8, the Manager, launcher, BaseTask, TaskMonitor
  and dead-task policy, SimpleBroker `db.py`, theory and invariants.
- Verdict: **BLOCK**. Every finding was checked against the source before its
  disposition.

| # | Finding | Verified | Disposition |
|---|---|---|---|
| 1 | P1: delete-then-disposition destroys proof; failure or crash after the delete is misread as a child win; SimpleBroker has no multi-queue transaction | Yes (`db.py` delete is its own transaction) | Fixed in plan: settle by exact move into the settler's queue; the trace survives until terminal envelope, ACK and checkpoint; resumable steps; probe (d) |
| 2 | P1: adding the keys to the identity check rejects child snapshots | Yes (`_adopt_scheduler_evidence` compares `evidence.get(key)`) | Fixed in plan: Manager-owned attempt evidence, outside the identity check |
| 3 | P1: same-TID requeue creates conflicting attempts across retained checkpoints | Yes (2,400 s minimum retirement age) | Fixed in plan by recommending F1 fail-closed: no same-TID relaunch from this path; requeue is recorded as out of scope |
| 4 | P2: the spec text contradicted itself about consumed-claim scheduling | Yes | Fixed in plan: the hold ends and custody is retained, worded consistently |
| 5 | P2: dead-task discovery rejects nonstandard suffixes; the custody lookup reads the child's own snapshot | Yes (`standard_task_queue_identity`, `_launch_custody_pending`) | Fixed in plan: the queue moved under the Manager TID; Manager-owned retirement rule; no TaskMonitor change |
| 6 | P2: probes (b), (c) and (g) could not falsify their defects | Yes | Fixed in plan: two hold points; loser identity and no-publication assertions with producer closure; (g) replaced by an orphan-retirement case; added (d) |
| 7 | P2: RECONSIDERATION REQUESTED — permanent legacy handling vs [WT-2a.9] / [ALT-WT-012] | Partly | Contested: "no claim" is D1's current no-evidence rule, not a second reader. The plan removes the dedicated legacy branch and test. Owner ruling F4 |
| 8 | P3: the "microseconds, no user code" assurance is unsupported because first-snapshot publication is best-effort | Yes (`_register_tid_state` returns False and execution continues) | Fixed in plan: the residual is restated without the assurance |

### Round 2 — scoped verification, same reviewer family and invocation

Verdict: **BLOCK**.
- Findings 2–8: RESOLVED.
- Finding 1: NOT RESOLVED.
- New findings 9–13, each checked against the source:

| # | Finding | Verified | Disposition |
|---|---|---|---|
| 1 | P1: after the final trace delete, a successor that re-adopts an older retained checkpoint sees no claim and no trace and misreads a completed settlement as "child started" | Yes (bootstrap merges predecessor residents) | Fixed in plan: the committed `launch_never_started` envelope is the permanent proof; classification checks it before treating absence as started; probe (d) adds the re-adoption case |
| 9 | P1: the F3 self-case moves within one queue, which SimpleBroker rejects (reviewer reproduced `ValueError`) | Accepted: the reviewer's reproduction was against the installed package; I did not rerun it | Fixed in plan: the settlement record moved to the child-keyed `T{tid}.never_started`, so source and target always differ; probe (e2) |
| 10 | P2: resumption deleted the trace from the wrong queue | Yes | Fixed in plan: the record has one fixed location, `T{tid}.never_started` |
| 11 | P2: after ACK, the configured terminal route is unknown | Yes (ACK deletes the reserved body) | Fixed in plan: `terminal_route` is recorded in the pre-launch checkpoint |
| 12 | P2: no deleter for the open claim of a receipted wrapper that dies before claiming | Yes | Fixed in plan: the Manager that ACKs or retires a resident exact-deletes its open claim; probe (e) |
| 13 | P2: the bootstrap gate conflicts with D5's establishing pass, and the fault expectation in (h) was wrong | Yes (`_admission_bootstrapped` is set after the loop) | Fixed in plan: bootstrap gate removed (the child-keyed record makes it unnecessary); (h) now expects publish-then-clear under a persisting fault |

Per the owner's one-scoped-round rule, no third review round was run. A
completed-work cross-family review remains mandatory before closure. If the
owner wants plan-level verification of the round-2 fixes first, run one
scoped check limited to findings 1 and 9–13.

### Two-factor fit test ([WT-7]) — pre-code

- Theory: committed `docs/program-theory.md` at `963dc83e`.
- Boundary: the §8 Proposed Spec Delta.
  - Plan names ("D5") were replaced by section names; plan-instruction lines
    were removed.
  - SHA-256 of the compared boundary: `3cd98d5bedf8755065e397176c34ed3a6e6b0a5f10c89831b514c2c576b15de2`.
- Author account: written first, before any other account existed.
- Blind account:
  - Model: codex-cli 0.161.0, model `gpt-6.1-sol` (OpenAI family; author is
    Anthropic).
  - Run from a scratch directory outside the repository with
    `--skip-git-repo-check -s read-only`.
  - The only inputs were the theory and the boundary, supplied inline.
  - The run log shows no tool calls.
  - Global Codex user configuration may still have applied; strict isolation
    from that is not claimed.
- Both accounts are valid: each names a refusal, a falsifiable prediction and
  a tension.

Author account, verbatim:

```text
Author [WT-7] account — launch-claim fence, boundary = plan §8 Proposed Spec Delta, theory = committed docs/program-theory.md (b60eeae3 lineage; HEAD 963dc83e).
Written before any blind account existed.

1. Verdict: fits with strain.

2. Sections the change rests on:
- [WT-2a.7] Exactly once per reservation; crash residue awaits explicit recovery. The fence turns the ambiguous pre-launch residue into explicit, positive recovery: a won claim proves the work never executed, so ACK after a definite terminal outcome keeps "exactly once" (here: zero times, reported) without guessing.
- [WT-2a.13] Death needs proof; absence is unknown. The change never converts absence into death. A won claim is proof of non-execution (not death); a consumed claim is proof of start (not liveness) and authorizes only eligibility, never ACK.
- [WT-2a.8] SimpleBroker owns atomic moves. Exclusion is exactly one SimpleBroker exact delete vs one exact move on one row; Weft adds no lock or transaction.
- [WT-2a.2] Queues are the durable truth. Every intermediate state is a queue row (open claim, never_started record, Manager envelope), so any successor reconstructs settlement from queues alone.
- [WT-2a.15] A standing refusal names the evidence that ends it. The fence supplies the ending evidence D1/D5 lacked for the dominant ambiguous case.
- [WT-4] consume-to-disappear queues; [WT-4.3] a quiet task costs nothing an operator can see: settlement runs in the existing held-resident pass; no new timer or waiter; both queues empty and vanish.

3. What it refuses and the entry that refuses it:
- Transparent replay of an ambiguous launch (owner ruling; [WT-2a.7]): fail-closed settles a proven-unstarted task as failed/launch_never_started; requeue and same-TID relaunch are refused.
- A permanent legacy reader ([ALT-WT-012], [WT-2a.9]): references without a claim fall under the single current no-evidence rule, with no dedicated decoder.
- A polling/retry timer ([ALT-WT-002]): failed reads change nothing and wait for the existing due pass.
- An unconsumed state table ([ALT-WT-019]): claim and record rows are consumed; nothing accumulates.

4. Falsifiable predictions:
- Kill the launching Manager before Process.start: a successor commits exactly one launch_never_started envelope, ACKs the exact reservation, and the target runs zero times. Any execution, second envelope, or retained reservation falsifies.
- A child that loses the race publishes zero task-state, outbox, ctrl_out or log rows and exits; any such row falsifies.
- Killing the settler after each disposition step (including during bootstrap, and with a successor re-adopting an older checkpoint) converges on one envelope, one ACK, no never_started row, and never classifies a completed settlement as "child started".
- An absent claim never yields ACK, replay or terminal inference.
- No new wake source: a held-only Manager's turn count is unchanged; per-launch cost is +2 broker operations, and median launch latency rises <=10% on SQLite and PostgreSQL.

5. Sentence that fits worst:
[WT-4.4] "As few concepts as possible." The change adds two queue names (unstarted, never_started), a three-read classification, two checkpoint evidence keys and a new terminal error. The [WT-7] falsifier "when a proposal invents a new queue lane ... treat that as a falsifier signal" also presses: these are not work lanes (no work flows through them; each row is a one-shot fence), and the underlying CS problem is named (fencing / test-and-set for ownership), but the reader must accept that distinction. A second strain is [WT-2a.15]'s "names the evidence or the operator action that ends it": the residual state (claim consumed, no snapshot, no receipt) still has neither, and stays held as custody without a supported release.
```

Independent account, verbatim:

```text
1. **Verdict: fits with strain.**

   The change makes an ambiguous launch settleable through a one-shot queue fence. The launched process must consume the claim before publishing anything; a settling Manager must win that same claim before declaring `launch_never_started`. Atomic competition over an existing queue primitive supplies the proof that missing process evidence cannot.

   The strain is that a consumed claim establishes permission to proceed, not actual task execution. A process can delete its claim and die before constructing its task. The change releases the scheduling hold in that case while preserving unresolved custody and forbidding replay. It improves progress without guaranteeing eventual settlement.

2. **The sections of the theory the change rests on.**

   [WT-2.5] supplies the classical model: crash-safe custody handoff. The claim extends that model with mutual exclusion between starting and settling. [WT-2a.7] supports retaining the reservation until acknowledgement or explicit disposition; releasing scheduling eligibility alone does neither.

   [WT-2a.2], [WT-2a.8], and [WT-4.2] place durable coordination in queues and atomic queue mechanics in SimpleBroker. The boundary uses exact deletion and movement rather than implementing a separate locking system. [WT-4.1] and [ALT-WT-019] support the queues’ consumption rules: claims and settlement records disappear as their work completes.

   [WT-2a.13] and [WT-4.3] require separating eligibility, custody, and death proof. A consumed claim releases eligibility without proving a live process, authorizing custody acknowledgement, or proving death. [WT-2a.15] supports holding the ambiguous launch and its descendants while allowing unrelated roots and sibling branches to dispatch, subject to named resource and durability barriers.

3. **What the change refuses and the theory entry that refuses it.**

   It refuses replay or a failed terminal inference merely because a task snapshot or process receipt is missing. [WT-2a.13] refuses treating absence as death; [WT-2a.7] refuses guessing the disposition of an unsettled reservation. `launch_never_started` instead requires winning the launch fence, which prevents the delayed process from executing.

   It refuses using Manager-local worker state as sufficient cross-Manager proof. [ALT-WT-015] rejects local child tracking as authoritative liveness or lineage evidence.

   It also refuses letting one ambiguous launch silently block unrelated work. [WT-2a.15] permits a broad hold only when a published, named resource, custody, or durability barrier requires it. The boundary preserves the failed-checkpoint barrier rather than claiming every hold can safely become local.

4. **What it predicts that could prove it wrong.**

   Race a delayed launched process against settlement. If the Manager moves the claim first, subsequently releasing the process must produce no task-state, outbox, `ctrl_out`, log publication, or user-work effect. Any such effect disproves the fence’s safety claim.

   Let the process consume the claim, then kill it before task construction or receipt publication. A successor must release the scheduling hold without replaying the request, emitting `launch_never_started`, or acknowledging custody merely from claim absence. Any of those actions would collapse distinctions the fit depends on.

   Kill the Manager after each settlement step: claim movement, terminal publication, reservation acknowledgement, and record deletion. Successors must finish settlement without conflicting terminal outcomes, duplicate logical settlement, or permanently stranded settlement records.

   On both SQLite and PostgreSQL, retain one ambiguous launch while providing measured headroom and unrelated accepted work. That work must remain dispatchable under normal frontier rules. Failed required reads must preserve the unresolved state. A context-wide stall attributable solely to that resident would contradict the claimed refusal scope.

5. **The sentence of the theory that fits worst, quoted, and why.**

   “A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death.” [WT-4.3]

   The boundary deliberately gives absence evidentiary force: a claim missing from both queues, with no settlement envelope, counts as started evidence. This fits only because a successfully read, previously established fence has a constrained consumption protocol, and because “started” here releases eligibility rather than proving execution or death. The process may have died immediately after deletion. That unresolved case is the sharpest tension: scheduling can recover while custody remains indefinitely unknown.
```

Comparison and classification:

| Agreement or divergence | Classification | Action |
|---|---|---|
| Both say fits with strain. Both rest on [WT-2a.7], [WT-2a.13], [WT-2a.8], [WT-2a.2] and [WT-2a.15]. Both name the same residual: claim consumed, no snapshot, custody unresolved indefinitely. Both predict loser silence, crash convergence after each settlement step, and refusal scope | Agreement: the theory transfers the intended account | None |
| Independent worst sentence is [WT-4.3] "A missing snapshot ... is `unknown`, never proof of death": the boundary gives *absence* of the claim row evidentiary force | Change deficiency (wording). The inference is sound only because the fence's deleters are enumerated, and the boundary did not say so | Fixed in plan: §8 and §4 now state why absence counts and what it does not prove, and name the operator-delete case as the safe-direction exception. No theory change |
| Author's worst sentence is [WT-4.4] concept count and the lane falsifier; the independent account does not raise it | Not a divergence in verdict; the independent account is silent on it | Kept as the author's recorded strain for the owner |
| Independent account cites [WT-2.5], [WT-4.1], [WT-4.2] and [ALT-WT-015]; the author cites [ALT-WT-002], [ALT-WT-012] and [ALT-WT-019] | Complementary citations, no contradiction | None |

The post-comparison wording fix changed the boundary. Current §8 SHA-256:
`50166a921a660c0200ade132ceb8af7b14c79087e764a05cfe9787d0d997471d`. The
accounts above are tied to the earlier hash. The fit test is repeated on the
final implementation diff (Task 6).

### Two-factor fit test ([WT-7]) — round 2, after the wording fix

- Theory: committed `docs/program-theory.md` at `963dc83e`.
- Boundary: §8 after the rewritten absence sentence, prepared as in round 1.
  SHA-256: `7dc236260755b2a00caa7df0dd2041cae34fefb80dc6c4d5246668b8a94931fb`.
- Author account: written fresh, before the round-2 blind account existed.
- Blind account:
  - Model: codex-cli 0.161.0, `gpt-6.1-sol`.
  - Run in a new scratch directory outside the repository, with only the
    theory and the boundary supplied inline.
  - No tool calls in the run log. The same isolation caveat as round 1
    applies.
- Both accounts are valid.

Author account, verbatim:

```text
Author [WT-7] account, round 2 — launch-claim fence, boundary = plan §8 Proposed Spec Delta (SHA-256 7dc236260755b2a00caa7df0dd2041cae34fefb80dc6c4d5246668b8a94931fb), theory = committed docs/program-theory.md at 963dc83e.
Written before the round-2 blind account existed.

1. Verdict: fits with strain.

2. Sections the change rests on:
- [WT-2.5] / [WT-2a.7] Two-phase custody; exactly once per reservation; crash residue awaits explicit recovery. A won claim is explicit, positive recovery: the request is settled failed/launch_never_started and ACKed, never replayed and never guessed.
- [WT-2a.13] Death needs proof. A won claim proves non-execution, not death; a taken claim proves start, not liveness, and releases only eligibility.
- [WT-2a.8] SimpleBroker owns atomic moves: exclusion is one exact delete against one exact move on one row.
- [WT-2a.2] Queues are the durable truth: the open claim, the never_started record and the Manager envelope reconstruct every intermediate state for any successor.
- [WT-2a.15] A standing refusal names the evidence that ends it: the fence supplies ending evidence for the dominant ambiguous case.
- [WT-4.1] consume-to-disappear and [WT-4.3] quiet: settlement uses the existing held-resident pass; both queues empty and vanish.

3. What it refuses and the entry that refuses it:
- Replay or terminal inference from a missing snapshot or receipt ([WT-2a.7], [WT-2a.13]); requeue and same-TID relaunch are excluded.
- Manager-local tracking as cross-Manager proof ([ALT-WT-015]): settlement rests on queue rows only.
- A permanent legacy reader ([ALT-WT-012], [WT-2a.9]): references without a claim fall under the single no-evidence rule.
- A diagnostic or retry timer ([ALT-WT-002]); an unconsumed table ([ALT-WT-019]).

4. Falsifiable predictions:
- Manager killed before Process.start: exactly one launch_never_started envelope, exact ACK, zero target executions.
- A child released after losing the race publishes zero task-state, outbox, ctrl_out or log rows.
- Settler killed after each disposition step, during bootstrap, and with a successor re-adopting an older checkpoint: one envelope, one ACK, no stranded record, and never "started" for a settled launch.
- A taken claim never yields ACK, replay or a terminal record.
- With an ambiguous resident held, unrelated work dispatches on SQLite and PostgreSQL; no new wake source; +2 broker operations per launch and <=10% median launch-latency rise.

5. Sentence that fits worst:
[WT-4.3] "A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death." The change lets the absence of a row count as start evidence. The boundary now states why (the checkpoint records the row's writing, and only the launched task removes the open claim of a still-held resident) and limits the inference to eligibility, but it remains an inference from absence that depends on every other deleter keeping to the enumerated rules. Close second: [WT-4.4] "As few concepts as possible": two queue names, a three-read classification, two evidence keys and a new terminal error.
```

Independent account, verbatim:

```text
1. Verdict: **fits with strain.** The change realizes crash-safe launch settlement with an atomic, one-shot claim over existing queue primitives. Winning the claim prevents the launched process from starting; consuming it establishes only that the process passed the launch fence. The strain is that recovery now depends on a precise account of every way that claim can disappear. Queue absence carries meaning only because the checkpoint and deletion rules constrain its causes.

2. The sections the change rests on.

   [WT-2.5] supplies the model: two-phase reservation custody, rather than replay based on uncertainty. [WT-2a.2] and [WT-2a.8] support durable queue evidence and SimpleBroker-owned atomic moves. [WT-2a.7] supports retaining an unsettled reservation independently of its Manager and refusing duplicate execution.

   [WT-2a.13] supports separating started evidence, custody handoff, and proof of death. [WT-2a.15] supports holding the ambiguous launch and its descendants while allowing unrelated roots and sibling branches to dispatch, subject to named resource or durability barriers. [WT-4.1] and [ALT-WT-019] support consuming open claims and settlement records as their work completes. These queues have a bounded coordination purpose rather than becoming a second lifecycle database.

3. What the change refuses and the entry that refuses it.

   It refuses to turn a missing snapshot or process receipt into permission to replay or declare death. [WT-2a.13] and [WT-4.3] refuse that inference. Even a consumed claim releases only scheduling eligibility; custody remains unresolved.

   It refuses a context-wide scheduling freeze caused solely by one ambiguous launch. [WT-2a.15] requires the refusal to stay scoped unless a named resource, custody, or durability barrier requires more.

   It also refuses retaining launch records indefinitely after settlement. [ALT-WT-019] rejects unconsumed runtime tables requiring later garbage collection. The cleanup clauses are therefore part of the fit, not optional housekeeping.

4. What it predicts that could prove it wrong.

   On both SQLite and PostgreSQL, race the launched process’s exact deletion against the Manager’s exact move. If the move succeeds, the process must publish nothing and execute no user work. If the process takes the claim, settlement must not produce `launch_never_started`. Either outcome occurring together falsifies the launch fence.

   Kill the Manager after each settlement step, then start a successor. Recovery must finish with the failed envelope on the recorded route, the exact reservation acknowledged, and the settlement record removed, without replay or conflicting terminal outcomes.

   Separately, let the process consume its claim and exit before publishing a snapshot. The successor must release the ancestry eligibility hold while retaining custody uncertainty. With measured capacity available and no separate barrier, unrelated roots and sibling branches must dispatch. Broker read or move failures must neither release custody nor manufacture terminal evidence.

5. The sentence that fits worst, quoted, and why.

   > “A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death.”

   This sentence from [WT-4.3] presses hardest against Manager-authored terminal settlement after a snapshot is missing. The distinction that preserves fit is positive exclusion: successfully moving the claim proves the process cannot pass its required launch fence. It does not infer death from silence. Conversely, claim absence establishes only passage through that fence under the specified deletion rules. If cleanup can erase a still-held resident’s claim without preserving settlement evidence, that distinction fails and the change no longer fits.
```

Comparison and classification:

| Agreement or divergence | Classification | Action |
|---|---|---|
| Both say fits with strain and rest on the same sections ([WT-2.5], [WT-2a.2], [WT-2a.7], [WT-2a.8], [WT-2a.13], [WT-2a.15], [WT-4.1], [ALT-WT-019]) | Agreement | None |
| Both choose the same worst sentence, [WT-4.3] "A missing snapshot ... is `unknown`, never proof of death". Both name the same hinge: a won move is positive exclusion, not death inferred from silence; absence means only passage through the fence, under the enumerated deletion rules | Agreement: the round-1 divergence is closed by the wording fix | None |
| Both name the same falsifier: cleanup that erases a still-held resident's claim without settlement evidence would break the fit | Agreement | Already covered by the retirement table, probe (g) and §4's operator-delete note |
| Author adds [WT-4.4] (concept count) as a close second strain; the independent account does not raise it | Not a verdict divergence | Kept as the author's recorded strain for the owner |

No document deficiency and no change deficiency were found in round 2. The
boundary at the SHA above is the reference for the Task 6 repeat on the
final implementation diff.

## Revision Log

| Date | Baseline | Change | Reason |
|------|----------|--------|--------|
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | Absence sentence rewritten; fit test round 2 recorded (converged) | Owner asked to update the wording and re-run |
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | Pre-code two-factor fit test recorded; absence-as-started wording fix | [WT-7]; independent account's [WT-4.3] tension |
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | F3 and F4 decided as recommended | Owner ruling |
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | F1 decided: fail-closed; note possible future `weft task retry TID` | Owner ruling |
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | Renamed the claims queue `T{manager_tid}.launch_claims` to `T{manager_tid}.unstarted` | Owner naming ruling (F2) |
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | Renamed the settlement queue `T{tid}.launch_settled` to `T{tid}.never_started` | Owner naming ruling (F2) |
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | Round-2 revision: child-keyed `T{tid}.never_started` record, envelope as permanent proof, recorded terminal route, open-claim delete on ACK, bootstrap gate removed, probes (d)/(e2)/(h) | Codex round-2 findings 1, 9–13 |
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | Round-1 review revision: Manager-owned claim queue, move-based resumable settlement, fail-closed F1, attempt evidence outside the identity check, rewritten probes, Manager-owned retirement | Codex BLOCK findings 1–8 |
| 2026-11-05 | 963dc83e + D1 delta + D5 draft delta | Sequence after D5: Task 0, D5 interaction, D5 descriptor blocks, probe (h) | Owner assigned D5 for implementation first |
| 2026-11-05 | 963dc83e + D1 delta | Initial draft | Owner asked for a plan resolving D1/D5's permanent ambiguous hold |

## Execution log

- Plan drafting only. No implementation, spec promotion or runtime mutation.

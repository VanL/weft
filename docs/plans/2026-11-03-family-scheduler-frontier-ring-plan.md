# Family Scheduler: Frontier Ring and Contracted Ancestry

Status: promoted
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8] (one sentence added; mapping bullet added); docs/specifications/07-System_Invariants.md [MANAGER.18] (the same sentence, because its paragraph is a verbatim copy of the [MA-1.8] paragraph), [MANAGER.16] and [OBS.6] (unchanged, constraint); docs/specifications/05-Message_Flow_and_State.md [MF-5] (unchanged, constraint); docs/program-theory.md [ALT-WT-016] (owner-gated wording)
Superseded by: none

Class: 5. Materiality requires a spec change: the plan adds one normative
sentence to [MA-1.8] (and the same sentence to [MANAGER.18], which repeats
the [MA-1.8] paragraph word for word) stating the rotation order among
branches, which today's text leaves unstated and today's code realizes as an
accident of its rebuild. The plan also proposes an [ALT-WT-016] wording edit,
which needs owner acceptance like every `[ALT-WT-*]` edit. Risky triggers
also fire: the change alters the Manager's exit and recovery execution path
on the durable spine (`Manager ->` frontier bookkeeping `->` the Manager
checkpoint in `weft.state.tasks.<manager_tid>`), and that path runs in four
Manager contexts: positive-exit retirement, startup-snapshot handoff,
periodic reconciliation, and the bootstrap sweep. So the hardening-plans
checklist applies and independent review precedes implementation. Plan type:
implementation with spec revision. Promotion strategy: **B (atomic)** for
`docs/specifications/03-Manager_Architecture.md` and
`docs/specifications/07-System_Invariants.md`; the theory edit lands in the
same commit if the owner accepts it, and not at all otherwise.

Series position: plan 5 of the 2026-11 cleanup series. It closes audit row
"4.2-a Classical CS models over blunt ad-hoc fixes" in
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (line 5450,
routed to plan 5 at line 5547).

Relation to the replaced draft: this file replaces
`2026-11-02-family-scheduler-incremental-retire-plan.md` (untracked, never
committed, removed in the same change that adds this file). That draft kept
today's longest-first frontier order byte for byte, so it could reach only
"total path length plus a sort" with a rent-or-buy prefix helper
(`_maximal_paths`). On 2026-11-03 the owner ruled that launch order is not a
guarantee and accepted the design below. The old draft's measurements,
current-structure notes, hidden couplings, test inventory, and execution
evidence were re-verified at `b7eed9f9` and are reused here where they still
apply (execution log E1, E2, E4, E6, E7).

## 1. Goal

`FamilyScheduler` in `weft/core/family_scheduling.py` keeps every maximal
live ancestry path as a materialized list (`_stacks: list[list[str]]`, line
246) and a plain index into it (`_turn`, line 247). Every positive exit
(`retire`, lines 386–403) removes the exiting TID from every stack, re-sorts
all stacks longest-first, and runs a quadratic prefix scan. Every recovery
(`finish_recovery`, lines 421–431) rebuilds all paths the same way. Measured
at `b7eed9f9`: going from 256 to 1,024 sibling stacks multiplies the
instruction count of one `retire` by 15.8, where linear work would multiply
it by about 4 (execution log E2). One `retire` on 1,024 siblings takes
about 27 ms (E1).

This plan replaces the two stored structures with two textbook ones:

- a **union-find with path compression** (`_up`) that answers "nearest live
  ancestor" under deletions, which is exactly the "contracted
  scheduling-parent pointer" that [MA-1.8] ("Cache contracted
  scheduling-parent pointers; do not derive a full graph per dispatch") and
  [ALT-WT-016] already describe, and which the same file already uses for
  pending buckets (`_PendingIndex.owner`, lines 75–83);
- a **circular doubly linked ring with a cursor** (`_ring_next`,
  `_ring_prev`, `_ring_tip`, `_turn`), the classic round-robin run-queue
  structure, with one entry per childless resident, so an exit touches only
  its own stack (an O(1) unlink or an in-place tip replacement) and `select`
  walks from the cursor and stops at the first hit, as today's index cursor
  does.

A stack's path is no longer stored. `frontiers` derives it, for the Manager
checkpoint, from the tip's own `ancestry_tids` filtered to live residents,
which is how `finish_recovery` already builds a path today. After the
change the frontier bookkeeping of a childless exit and of an interior exit
is one pointer lookup, amortized O(log n) per lookup under path compression
alone (n the pointer-table entries, at most twice the registered
residents), with no ranked union added (adding one would be a mechanism the
measured gain does not need), plus O(1) count and ring updates; the whole
`retire` still carries today's pending-index meld (O(log n) in the melded
requests) and root-heap re-offer (O(log R)). An exit updates its own path
and, when the pointer table has grown past twice the registered count,
triggers one whole-table compaction charged to the exits that created the
dead entries. A launch's bookkeeping is one such lookup; the whole
`launched` also scans the trailing ancestry entries the scheduler has never
seen (never more than today's full-ancestry filter). `select` costs what it
costs today (it stops at the first hit: O(1) for a hit at the cursor, one
walk of S stacks on a full miss; §10 FE-2 records why the first draft's
snapshotting dict ring was rejected), a recovery costs time linear in
residents times depth, and the quadratic term is gone on every tree shape.

Observable change, ruled by the owner on 2026-11-03: the rotation among
branches becomes a ring with a cursor instead of longest-first re-sorted on
every exit. A new path joins the rotation last: it is inserted immediately
before the path the cursor names next, so it is visited after every path
already waiting; a path keeps its place when it extends or contracts; a
recovery rebuild orders paths in resident adoption order and restarts the
cursor at the first of them (§10 Owner Decision 2 carries the spec wording).
This is not chronological creation order: after creating A, B, C, selecting
A (the cursor moves to B) and creating D, the rotation from the cursor is
B, C, A, D, where creation order would be B, C, D, A. Everything [MA-1.8]
promises is kept: round-robin
turns, newest eligible tip children first, oldest independent family FIFO by
root ID, family siblings round-robin. The checkpoint `stacks` list keeps its
shape (a list of paths) and its contents as a set; only its order follows
the new rotation. The pending side (`_PendingIndex` leftist meld,
`_FamilyPending`, union-find `owner()`) is already aligned and does not
change.

Acceptance: the [MA-1.8] contract (Tasks 3–4 tests, Manager-level suites
green on SQLite and PostgreSQL) plus the measurement in Task 6: at or under
baseline on every swept shape within the control band, wide shapes at least
two orders of magnitude faster, the checkpoint payload proxy within the
control band on every shape, and `select` at or under baseline on the
head-hit and full-miss shapes.

## 2. Source Documents

Owner rulings (2026-11-03), verbatim. On the replaced draft's fork F1, which
kept today's longest-first order and so could reach only "linear plus a
sort":

> "So I don't think that we make guarantees about launch order anywhere. How
> exactly would the insertion-order stacks be implemented/stored?"

After the session lead described a design built on union-find for
nearest-live-ancestor under deletions and an insertion-ordered dict as a
round-robin ring, with paths derived at checkpoint time rather than stored,
and asked "Could this design be improved using standard computer science
algorithms? Is there a data structure or library that would be worth
using?":

> "Yes, that sounds correct."

Consequences: the replaced draft's helper (`_maximal_paths`, the rent-or-buy
switch), its fork F1, and its fork F3 are closed. Its fork F2 (the
[ALT-WT-016] wording) stays as Owner Decision 1.

Later owner rulings (2026-11-03, after the fresh-eyes pass), verbatim. On
the `_up` retirement rule, after the compaction rule was explained in full:
"Yes, compact." (§10 Owner Decision 3, closed). On open item O1, after the
owner asked whether under the current code a child could be registered
before its parent and the session lead answered no, for three reasons:
"record it as an assumption that is true under the current code, with a
note that if that changes, the implementation might need to adjust." (§4
"Assumptions", A1). Separately, the ring's data structure changed after the
fresh-eyes pass from the insertion-ordered dict the session lead first
described to a circular linked ring with a cursor, to remove a `select`
regression (§10 FE-2). The owner's acceptance above covers the concepts
(union-find pointers, a round-robin ring of childless residents, paths
derived rather than stored); the structure choice is FE-2's owner item.

Specs (normative, read at `b7eed9f9`):

- `docs/specifications/03-Manager_Architecture.md` [MA-1.8], the paragraph
  at line 445 that opens "Ordinary launch selection is work-conserving
  family scheduling over dynamically observed ancestry." It says "Active
  branches receive round-robin turns and offer newest eligible tip children
  first" and states no order among branches. The paragraph at line 502
  says "Cache contracted scheduling-parent pointers; do not derive a full
  graph per dispatch" and "In-memory selection and path bookkeeping scale
  with live ancestry". The paragraph at line 500 says "Background ancestry
  is transparent when locating a finite child scheduling parent", and line
  557 says "trusted internal ancestors are transparent to finite frontier
  selection". The `_Implementation mapping_` list starts at line 565; its
  first bullet (line 566) is the pending-index reclamation bullet; the
  admission bullet at line 575 names `weft/core/manager.py` as owner of
  "frontiers" in the sense of checkpoint custody and lists
  `weft/core/family_scheduling.py` under "Additional verification".
- `docs/specifications/07-System_Invariants.md` [MANAGER.18] (heading at
  line 1133). Its third paragraph (line 1139) is byte-identical to the
  [MA-1.8] paragraph at 03 line 445 (execution log E8). [MANAGER.16] (line
  1118): internal work precedes ordinary FIFO/LIFO family selection; this
  plan does not touch the internal lane, so it is unchanged. [OBS.6] (line
  388) lists the checkpoint fields: "`scheduler_frontiers` (`stacks`,
  `residents`, ... ): active frontier stacks, ..."; no order is stated, so it
  is unchanged.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (section heading
  at line 538; the checkpoint sentence at line 916 repeats the [OBS.6]
  wording). The `stacks` shape is unchanged and no order is stated, so no
  block is needed.

Theory (frames judgment; does not override specs):

- `docs/program-theory.md` [WT-2a.2] (line 274): queues are the only durable
  truth; process memory holds hints. Every structure this plan adds is a
  Manager-local hint rebuilt from `residents` by `finish_recovery`; recovery
  never reads `stacks` (§3, "Who reads `stacks`").
- [WT-4] 4.1 "Queues carry facts; processes carry hints" (line 415): "local
  Manager tables are caches/hints rebuilt from queues". 4.2 "Classical CS
  models over blunt ad-hoc fixes" (line 425). 4.4 "Measure; do not bake in
  inefficiency" and "No capability diminution".
- [ALT-WT-010] (line 681): "No heaps, counters, or registries exist only to
  claim constant cost. In-memory state stays a hint." Its "Reconsider when"
  reads "A cost measured on both SQLite and PostgreSQL remains after every
  existing primitive is used correctly, and the owner accepts a named new
  concept." See §4 invariant 6 for why this plan does not reopen it and,
  if a reviewer reads it otherwise, why both reopening conditions hold.
- [ALT-WT-016] (lines 777–798), in full: re-deriving the task graph per
  dispatch or bulk-moving pending tokens on exit is refused. Its "Current
  consequence" says the Manager "updates the bucket's contracted
  scheduling-parent pointer in $O(1)$ in memory". That is a bucket claim:
  the link is O(1) (`other.parent = self` in `_PendingIndex.merge`, line 97),
  the meld is O(log n) (`_meld`, lines 47–61). Owner Decision 1 proposes
  wording that separates the bucket link, the meld, and frontier
  contraction.
- [WT-7] (line 941): at class-5 completion the keyed possession probe and
  the theory-versus-code audit table refresh are owed (Task 6).

Plans:

- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (completed):
  audit row 4.2-a, routed here.
- `docs/plans/2026-10-14-measured-admission-family-scheduling.md` (index
  status `draft`; its admission gate, lineage transport, pending indexes,
  FIFO/LIFO reducer and launch custody are landed) and
  `docs/plans/2026-10-15-work-conserving-family-scheduling.md` (completed;
  removed the frontier cap). The frontier rebuild comes from these two
  plans. Neither sets a complexity bound for it.
- `docs/plans/2026-10-27-runtime-and-test-repair-plan.md` (completed) landed
  `_PendingIndex._compact`, whose `H > 2L` trigger is the in-file precedent
  for this plan's pointer compaction.

Guidance: `CLAUDE.md` "Shared Agent Context", "Project Conventions", §1.1
("Absence is not a gap"), §4, §5.1;
`docs/agent-context/decision-hierarchy.md` [DOM-15];
`docs/agent-context/runbooks/writing-plans.md`;
`docs/agent-context/runbooks/hardening-plans.md`;
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`;
`skills/call-agent/SKILL.md`.

## 3. Context and Key Files

### Current structure at `b7eed9f9`

`weft/core/family_scheduling.py` (431 lines; `git diff --stat b7eed9f9`
over it is empty, E7). The pending side (lines 22–221: `PendingRequest`,
`_Head`, `_meld`, `_pop`, `_PendingIndex`, `_FamilyPending`) and `_Resident`
(lines 223–227) do not change. `FamilyScheduler` (line 230):

- State (lines 237–247): `_pending`, `_parents` (bucket per scheduling
  parent), `_family_pending`, `_request_indexes`, `_family_counts`, `_roots`
  (heap of root IDs), `_indexed_roots`, `_residents: dict[str, _Resident]`,
  `_stacks: list[list[str]]`, `_turn = 0`.
- `frontiers` (lines 249–252): `tuple(tuple(stack) for stack in
  self._stacks)`.
- `_parent(root_tid, ancestry_tids)` (lines 261–264): the first live
  resident in `reversed(ancestry_tids)`, else `root_tid`. It stops at the
  first hit, so it costs one step per non-live trailing entry plus one.
  Callers: `add_pending` (line 271), `remove_pending` (line 291), `retire`
  (line 391).
- `_active_roots` (lines 305–306): the root of every stack's tip, O(S) for S
  stacks; called once per `_oldest_independent` (line 309).
- `select` (lines 325–352): for `offset` in `range(count)`, `index = (_turn +
  offset) % count`; ask the tip's bucket; on a hit set `_turn = (index + 1) %
  count` and return. Then `_oldest_independent()`. Then the same cursor loop
  asking each tip's family for its oldest sibling. A miss leaves `_turn`
  unchanged.
- `launched` (lines 354–378): `internal` returns; an existing resident with
  the same identity returns (a different identity raises `ValueError`);
  `path` = the request's `ancestry_tids` filtered to live residents (O(full
  ancestry)); `parent = path[-1]`; then scan every stack for one whose tip is
  `parent` and append to it, else append `[*path, tid]` as a new stack
  (O(S)). So a stack is exactly "a childless resident and its live
  ancestors".
- `release_background` (lines 380–384) calls `retire` for a background
  resident.
- `retire` (lines 386–403): pop the resident; `parent = _parent(...)`; merge
  the exiting TID's pending bucket into the parent's (`_PendingIndex.merge`,
  O(log n), tokens never move); remove `tid` from every stack; stable sort
  longest-first; keep each non-empty stack that is not a prefix of a kept
  one (`any(other[: len(stack)] == stack for other in maximal)`); re-offer
  the root. `_turn` is not adjusted, so it now indexes a re-sorted list.
- `recover` (lines 405–419): finite only; writes `_residents[tid]`; unless
  `defer`, calls `finish_recovery`.
- `finish_recovery` (lines 421–431): one path per resident from its filtered
  ancestry plus itself, then the same sort and prefix scan.

Manager call sites (`weft/core/manager.py`):

- `Manager._register_launched_frontier` (line 1645) calls `launched` (line
  1653); it is called from `Manager._commit_child_launch_success` (line
  1672, call at line 1701).
- `Manager._retire_scheduler_residents` (line 2757) calls `retire` (line
  2770), then `_publish_scheduler_checkpoint` once per batch.
- `Manager._advance_launch_custody` (line 2365) calls `recover` (line 2388,
  snapshot handoff, only for a TID not already resident) and
  `release_background` (line 2420).
- `Manager._adopt_scheduler_evidence` (line 2252) calls `recover` (line
  2301, reconciliation, only for a TID not already resident).
- `Manager._recover_scheduler_paths` (line 2614) calls `recover(...,
  defer=True)` per live resident (line 2622) and `finish_recovery` once (line
  2629). This is the only `defer=True` caller in `weft/` (E9).
- `Manager._peek_family_candidate` (line 5660) is the only caller of `select`
  (line 5662) and caches its result in `_family_candidate`.
- `Manager._build_tid_state_payload` (lines 1211–1225) is the only caller of
  `frontiers` in `weft/` (line 1217, `"stacks": [list(stack) for stack in
  scheduler.frontiers]`). It runs on every Manager task-state publication
  (`BaseTask._register_tid_state`, `weft/core/tasks/base.py` line 2436, call
  at line 2456). Its callers for a Manager: `_initialize_base_task_runtime`
  (base.py line 388), `_publish_projection_edge` (2085),
  `register_managed_pid` (2486), `register_runtime_handle` (2531),
  `_set_activity` (2632), `Manager._publish_scheduler_checkpoint` (manager.py
  line 1504, six call sites in manager.py), and
  `Manager._handle_child_launch_failure` (line 1774). The same payload also
  deep-copies `_scheduler_residents` (line 1220), whose entries carry each
  resident's full `ancestry_tids` list (E10).

Who reads `stacks` (the old draft's claim, re-verified, E10): nothing in
`weft/` reads the `stacks` list back, neither its contents nor its order.

- `Manager._bootstrap_scheduler_evidence` (line 2522) reads
  `payload.get("scheduler_frontiers")` at line 2528, requires a Mapping,
  and reads only `checkpoint.get("residents", {})`. It adopts each
  resident's evidence (`_adopt_scheduler_evidence`), then calls
  `_recover_scheduler_paths`, which rebuilds paths from residents.
- `Manager._recover_unlaunched_reservations` (line 2551) reads
  `payload.get("scheduler_frontiers")` at line 2558, requires a Mapping, and
  reads only `source_queue` and `reserved_queue`.
- `weft/core/task_state.py::manager_launch_custody_pending` (line 131) reads
  `residents` and `reserved_queue` only.
- No other reader exists in `weft/`, `bin/`, `integrations/`, or
  `extensions/`.

Tests that observe `frontiers` or `stacks` (E11): every multi-stack
assertion compares a length (`tests/core/test_family_scheduling.py` lines
80, 85, 143, 223; `tests/core/test_work_conserving_admission.py` line 61) or
a set (`tests/core/test_family_scheduling.py` line 123). Every exact-tuple
assertion has one stack or none (`tests/core/test_family_scheduling.py`
lines 72, 125, 186; `tests/core/test_manager_family_maintenance.py` lines
730, 850, 889, 905; `tests/core/test_family_dispatch_scaling.py` line 168;
`tests/core/test_family_source_reconciliation.py` line 122;
`tests/system/test_family_matrix.py` line 442). The other `stacks` mentions
in `tests/tasks/test_liveness_monitor.py` and
`tests/tasks/test_task_monitor.py` are fixture payloads with `"stacks": []`.
**No test asserts longest-first order.** No existing test is reclassified.
The author ran the whole of `tests/core/test_family_scheduling.py` against a
prototype of the new class: 24 of 24 passed unchanged (E12).

### Measured complexity at `b7eed9f9`

The reducer performs no broker I/O (class docstring line 234), so its
counted cost is the same on SQLite and PostgreSQL by construction. Shapes:
one root `100` with S live sibling leaves (L = 1), or S disjoint chains of
depth L below the root (R = S·L + 1 residents).

Instruction counts with `sys.monitoring` (E2, re-run 2026-11-03, identical
to the replaced draft's run):

| S | `retire(tip)` | `finish_recovery()` | `recover(new leaf)` |
|---|---|---|---|
| 64 | 37,518 | 38,652 | 39,829 |
| 256 | 567,534 | 572,124 | 576,565 |
| 1024 | 8,954,478 | 8,972,892 | 8,990,389 |

Wall time, best of five (E1, re-run 2026-11-03 with `perf_counter_ns`):

| case | S | L | best ns |
|---|---|---|---|
| retire_tip | 256 | 1 | 1,690,084 |
| retire_tip | 1024 | 1 | 26,895,750 |
| finish_recovery | 1024 | 1 | 27,315,625 |
| finish_recovery | 1024 | 4 | 116,328,917 |
| recover_nodefer | 1024 | 4 | 119,508,584 |

Derived bounds today: `retire` is O(S·L) filter + O(S log S) sort + O(S²·L)
prefix loop; `finish_recovery` is O(R·L) + O(R²·L);
`recover(defer=False)` repeats `finish_recovery`, so a handoff sweep that
adopts R residents one at a time is O(R³·L); `launched` is O(full ancestry
+ S); `frontiers` is a C-level copy of the stored stacks.

Narrow and wide shapes through the public reducer API, from the replaced
draft's E9 harness (2026-11-02, five interleaved rounds, values are the
range of round medians; Task 1 re-records them at the tree as it is):

| Shape | Baseline |
|---|---|
| `retire`, 1,024 siblings, depth 1 | 27.09–27.49 ms |
| `finish_recovery`, 1,024 branches, depth 4 | 118.7–120.3 ms |
| `retire` tip, one chain, depth 1,024 | 11.04–11.21 µs |
| `finish_recovery`, one 2,048-resident chain | 56.41–56.93 ms |
| `finish_recovery`, 2 chains, depth 1,024 | 29.80–29.97 ms |
| `finish_recovery`, 4 chains, depth 512 | 15.38–15.48 ms |
| `finish_recovery`, 16 chains, depth 128 | 5.82–6.09 ms |
| `retire` tip, 2 chains, depth 1,024 | 23.42–23.62 µs |
| `retire` tip, 4 chains, depth 512 | 26.73–26.88 µs |
| `retire` tip, 8 chains, depth 256 | 34.48–35.12 µs |
| `retire` tip, 16 chains, depth 128 | 50.44–51.19 µs |

That harness's noise band (an unmodified copy of the module compiled the
same way as the variants, against the imported module) was 0.968–1.051 per
round and 0.991–1.018 in per-shape medians; the timer resolution is 41.7 ns
(`mach_absolute_time`).

### The design

New `FamilyScheduler` state; `_stacks` is deleted, and `_turn` keeps its
name and role (where the next selection starts asking) but holds a stack id
instead of a list index:

```text
self._residents: dict[str, _Resident]   # unchanged
self._up: dict[str, str | None]          # union-find: an ancestor with no registered resident in between; None above a root
self._live_children: dict[str, int]      # residents whose nearest live ancestor is the key (registered residents only)
self._ring_next: dict[int, int]          # stack id -> next stack id (circular)
self._ring_prev: dict[int, int]          # stack id -> previous stack id
self._ring_tip: dict[int, str]           # stack id -> tip TID
self._tip_stack: dict[str, int]          # tip TID -> stack id (exactly the keys that are tips)
self._turn: int | None                   # the stack id to try first; None when the ring is empty
self._next_stack_id: int                 # monotonically increasing; ids are never reused
```

A resident is *registered* when it has a `_live_children` entry. Every
resident is registered except one adopted by `recover(..., defer=True)`
whose `finish_recovery` has not run yet (§4 invariant 8); `finish_recovery`
is the only operation that moves a resident from deferred to registered.
Every frontier lookup asks "registered?", never "resident?": `_find` (and
through it `_live_parent` and `_nearest_live`) stops only at a registered
resident and follows the pointer of any node that is not registered; only
the pending side's `_parent`, unchanged, still asks "resident?". So during a deferred window the incremental structures
(pointers, counts, ring) are consistent with the registered set, not with
`_residents`.

Invariants of the new state (the property tests, Task 3 T1 and T9, check
them after every step):

- I-R1. The tips in `_ring_tip` are exactly the childless registered
  residents: every registered resident `t` with `_live_children[t] == 0` is
  the tip of exactly one stack, and no other stack exists. `_tip_stack` is
  the inverse of `_ring_tip`.
- I-R2. Every registered resident has an `_up` entry; every other `_up` key
  is a former resident, or a deferred adoption that kept the entry it had
  as a former resident (not yet compacted). Following `_up` from any key
  reaches a registered resident or `None`, and the first registered
  resident reached is the key's nearest registered ancestor.
- I-R3. `_live_children[p]` is the number of registered residents whose
  nearest registered ancestor is `p`.
- I-R4. `len(_up) <= 2 * len(_live_children)` after every public operation
  (compaction, below); outside a deferred window that is `2 *
  len(_residents)`.
- I-R5. `_ring_next` and `_ring_prev` have exactly the keys of `_ring_tip`,
  are inverse to each other, and link those keys into one cycle; `_turn` is
  one of those keys, or `None` exactly when `_ring_tip` is empty.

Why a stack id and not the tip as the ring key: a contraction or extension
replaces a stack's tip while keeping the stack's place. Keying the links by
a stable stack id makes that an update of `_ring_tip[stack_id]` and
`_tip_stack`, with no relinking; `_tip_stack` maps a tip back to its id. Why
a linked ring and not a Python `dict` in insertion order: a dict ring can
rotate only by moving each visited entry (pop and reinsert, or
`OrderedDict.move_to_end`), and the first draft also snapshotted the whole
ring on every call, which made a hit at the head O(S) (§10 FE-2); the
linked ring rotates by moving `_turn` alone, needs no per-visit mutation, so
a hit at the cursor is O(1) and a full miss is one walk of S steps with two
dict lookups per step, as today. Why a count and not a children set: only "is it childless now" is ever asked, and
an exit with children must hand all its children to its parent at once;
adding two integers does that, where moving a set is O(children).

Operations (the exact code is in Task 4; this is the reasoning):

- `_find(tid)`: follow `_up` while the node is not registered (not in
  `_live_children`), then point every node on that chain at the result
  (path compression) and return the nearest registered resident at or
  above `tid`, or `None`. It follows the retained pointer of any node that
  is not registered, whether dead or in the deferred window, and compresses
  to the registered ancestor it reaches; a deferred resident is therefore
  invisible to incremental operations until `finish_recovery` registers it,
  exactly as a fresh deferred resident with no pointer already is (§4
  invariant 8; T13, T14, T15). Amortized O(log n) per lookup (n the `_up`
  entries) under path compression alone, with no ranked union added
  (adding one would be a mechanism the measured gain does not need).
  The in-file precedent is `_PendingIndex.owner()` (lines 75–83); the theory
  calls the idea a "contracted scheduling-parent pointer" ([ALT-WT-016];
  [MA-1.8] line 502).
- `_live_parent(tid)` for a resident: `_find(_up[tid])`, stored back into
  `_up[tid]`.
- `_nearest_live(ancestry_tids)`: scan `reversed(ancestry_tids)` to the first
  TID that has an `_up` entry and return `_find` of it, else `None`. This is
  the nearest registered ancestor of a request: an entry whose TID is not
  registered (a former resident, or a deferred adoption that kept its
  pointer) is resolved by `_find` past it. `ancestry_tids[-1]` is the
  submitter's nearest finite ancestor, not always the immediate parent
  (`weft/core/runtime_identity.py::submission_lineage`, line 237, appends
  the submitter's own TID only when it is finite, so background and internal
  submitters are transparent, as [MA-1.8] lines 500 and 557 require). Every
  TID in the tuple is a finite ancestor. Trailing TIDs the scheduler has
  never held (launched by another Manager, exited before this Manager's
  life, or dropped by compaction) have no `_up` entry; the scan steps over
  them, as today's `_parent` does. Cost: one step per trailing unknown TID
  plus one amortized O(log n) lookup. This plan does not seed `_up` with never-resident ancestors (§4,
  design decision D1).
- `launched(request, class)`: identity checks as today. `p =
  _nearest_live(ancestry)`; record the resident; `_up[tid] = p`;
  `_live_children[tid] = 0`. If `p is None`: start a stack. Else
  `_live_children[p] += 1`, and if `p` was a tip (`p in _tip_stack`), the
  stack's tip becomes `tid` in place (`_ring_tip[stack_id] = tid`, same ring
  position); else start a new stack. Starting a stack (`_start_stack`) takes
  `_next_stack_id`, increments it, and links the new id at the ring's tail,
  just before `_turn`, in O(1), so the next selection asks it after every
  existing stack; in an empty ring the id links to itself and becomes
  `_turn`. A parent that has no `_up` entry (a TID this scheduler never held,
  one dropped by compaction, or a fresh deferred adoption) is stepped over
  by `_nearest_live` as unknown; a parent that has an entry but is not
  registered (a former resident, or a deferred adoption that kept its
  pointer as a former resident) is resolved by `_find` past it. Either way
  a launch under a deferred parent attaches the child to the nearest
  registered ancestor (round-1 witness: 103 launched beneath the deferred
  101 attaches to 100), and the pending `finish_recovery` repairs pointer,
  counts and ring (§4 invariant 8; T14, T15).
- `retire(tid)`: two jobs, two parents. Pending-token custody follows
  residents; frontier bookkeeping follows the registered set (§4 invariant
  8). First, pop the resident and merge its pending bucket exactly as
  today: `parent = _parent(resident.root_tid, resident.ancestry_tids)`, the
  first resident in the ancestry whether registered or deferred, else the
  root; when `tid` is the root itself, `parent == tid` and nothing merges;
  otherwise `self._parents.setdefault(parent, _PendingIndex()).merge(source)`,
  today's code unchanged. Second, `k = _live_children.pop(tid, None)`. If
  `k is None`, the resident is a deferred adoption, with or without a
  retained pointer: touch no pointer, count or stack; it cannot be in the
  ring, and the pending `finish_recovery` rebuilds (T13). Otherwise `p =
  _find(tid)` (after the pop from `_live_children`, so `tid` is no longer
  registered and the walk resolves above it; a deferred ancestor on the way
  is followed past, so in the round-1 witness the exit of 102 beneath the
  deferred 101 resolves to 100 and the stack contracts to 100, while 102's
  bucket merges into the deferred 101, T13). The two parents differ only
  inside a deferred window, where `_parent` may return a deferred resident
  that `_find` follows past. Merging the bucket into `p` there would move
  pending custody, and `finish_recovery` rebuilds frontier structures,
  never buckets (round-2 witness: with request 150 beneath the deferred
  101 and 160 beneath 102, the exit of 102 would put 160 in 100's bucket,
  and `select` after recovery would return 150 where today returns 160;
  E26, T13). Then `_contract(tid, p, k)`: if `p is not None`: `_live_children[p] += k -
  1` (the parent loses `tid` and gains its `k` children). If `tid` was a tip
  (childless): if `p` is not `None` and `p` is now childless, the stack
  contracts to `p` in place (`_ring_tip[stack_id] = p`, position
  preserved); else the stack is unlinked (`_drop_stack`, O(1): its
  neighbours are linked to each other, and when it was `_turn`, `_turn`
  moves to its successor, or to `None` when the ring empties), because its
  remaining path is a prefix of a sibling's, or `tid` was a root. If `tid`
  had children, the ring is unchanged, because the set of childless
  residents did not change; its children resolve to `p` on their next
  lookup through `_up[tid]`. Then `_compact_up()`, then re-offer the root as
  today.
- `_compact_up()`: when `len(_up) > 2 * len(_live_children)`, rebuild `_up`
  to hold only the registered residents, each pointing at its live parent
  (`_live_parent` through the old map), R lookups. It counts and rebuilds
  registered residents only (`_live_children`, not `_residents`; the Task 4
  code iterates `_live_children`, and E25 shows a compaction inside a
  deferred window keeping only registered keys): a deferred adoption is not
  in the rebuilt table, and a pointer it kept as a former resident is
  dropped with the other unregistered entries, after which `_nearest_live`
  steps over it like a fresh deferred adoption. This is `_PendingIndex.remove`'s
  `physical_count > 2 * count` trigger (line 162) applied to pointers: at a
  compaction more than half of the entries are dead, each dead entry was
  created by one exit since the previous compaction, so the rebuild is
  charged to those exits, adding amortized O(1) lookups per exit. After a
  compaction no dead entry remains, so no pointer can lead to a missing key;
  a later lookup for a request whose ancestry names a compacted TID scans
  past it to a resident (that is `_nearest_live`'s trailing scan). The owner
  chose this rule on 2026-11-03 ("Yes, compact."; §10 Owner Decision 3,
  closed).
- `select()`: with an empty ring (`_turn is None`), return
  `_oldest_independent()` (today's two loops run zero times, so this is the
  same). Otherwise, starting at `_turn`, follow `_ring_next` for at most S
  steps (S = `len(_ring_tip)`), asking each tip's bucket; on the first
  candidate set `_turn` to that entry's successor and return it. Then
  `_oldest_independent()` as today. Then the family-sibling pass the same
  way, again from `_turn` (unchanged by the miss). Nothing is moved or
  relinked. Equivalence with the cursor: today the search order is the
  cyclic list order starting at `_turn`, and a hit at position `i` makes the
  next search start at `i + 1`; the linked ring's search order is the cyclic
  link order starting at `_turn`, and a hit makes the next search start at
  the hit's successor; a miss leaves `_turn` unchanged in both. So for the
  same inputs, while no stack is created or deleted between selections
  (in-place extension and contraction allowed), both choose the same
  sequence (E13 for the first draft, E22 for the linked ring: 500 of 500
  seeds against the real baseline class; Task 3 T6 proves it against cursor
  arithmetic). They differ when a launch starts a new stack: today the new
  stack sits at list index `n`, so it is asked after the stacks from `_turn`
  to `n - 1` and before `0` to `_turn - 1`; in the ring it is linked just
  before `_turn`, so it is asked after every existing stack. They also
  differ after a deletion, because today's `retire` re-sorts the list under
  an unadjusted index, while the ring passes a deleted `_turn` to its
  successor (Task 3 T11 pins the ring's edit rules).
- `frontiers`: `tuple((*filter(residents.__contains__,
  residents[tip].ancestry_tids), tip) for tip in self._rotation_tips())`,
  where `_rotation_tips()` walks `_ring_next` from `_turn` (S steps) and
  returns the tips in rotation order. Order matters here (register row 8:
  the checkpoint lists stacks in the order the next selection would ask
  them), so this walks the ring rather than iterating `_ring_tip.values()`.
  `filter` with the bound C method `__contains__` keeps the scan at C
  speed. Cost per call: the total ancestry length of the tips (live and
  non-live entries). The same payload already deep-copies every resident's
  full `ancestry_tids` (manager.py line 1220) in Python, so the derivation
  is bounded by the work the write it feeds already does. It is still more
  work than today's tuple copy of stored stacks: the author prototype
  measured 1.4 µs against 0.4 µs (one chain, depth 64), 17 µs against
  2.8 µs (one chain, depth 1,024), and 180 µs against 126 µs (1,024
  siblings), against a payload proxy of 0.25 ms, 30.4 ms, and 2.3 ms on the
  same shapes (E14). The linked ring's walk adds a Python loop of S steps:
  the linked candidate measured 1.5 µs (depth 64), 19.7 µs (depth 1,024)
  and 157 µs (1,024 siblings, against 132 µs for the dict ring and 62 µs
  for today's copy in the same run) (E24). Task 6 gates the payload proxy,
  not `frontiers` alone. An `_up`-chain walk was also prototyped; it is
  1.7–4.8× slower than the filter on these shapes (E14) and is rejected (§4
  "Alternatives considered").
- `_active_roots()`: as today, over `self._ring_tip.values()` (order does
  not matter: it builds a set), O(S) per `_oldest_independent` call. A root-count map would make it O(1); it is not
  added (§1.1 "Absence is not a gap") unless Task 1 or Task 6 shows it
  dominates a measured shape; record the decision in the execution log.
- `recover(..., defer)`: unchanged (its docstring states the deferred
  window, §4 invariant 8). `finish_recovery()`: rebuild every frontier
  structure from `_residents` in one pass each: `_up[tid]` = the first
  resident in `reversed(ancestry_tids)` (or `None`), the counts from those
  pointers, and the ring from the childless residents in `_residents`
  insertion order, linked in that order, with `_turn` set to the first of
  them (so a recovery resets the rotation; register row 1). `_next_stack_id`
  keeps counting; ids are never reused. O(R·L). Former residents and their
  pointers are dropped.
- `_parent(root_tid, ancestry_tids)`: kept unchanged, and still the
  pending-bucket rule everywhere it is used today: `add_pending`,
  `remove_pending`, and `retire`'s merge target. Its contract is "the first
  live resident in the ancestry, else the root", deferred residents
  included. Outside a deferred window `_find` with the root fallback gives
  the same answer when lineage is consistent (the property test asserts
  that `_live_parent` equals the nearest-resident scan for every resident
  after every step, Task 3 T9); inside one the two can differ, and
  `retire` uses each for its own job (above).

Complexity after the change:

| Operation | Today (whole method) | After: frontier bookkeeping | After: whole method |
|---|---|---|---|
| `launched` | O(full ancestry + S) | one `_find`, amortized O(log n), plus O(1) pointer, count and ring updates | the bookkeeping plus the ancestry scan over trailing unknown entries (at most the full ancestry; usually one step) |
| `retire`, childless or interior | O(S·L + S log S + S²·L), plus the pending-index meld and root re-offer | one `_find`, amortized O(log n), plus O(1) count and ring updates; when `len(_up)` exceeds twice the registered count, one whole-table compaction (R lookups) charged to the exits that created the dead entries | the bookkeeping plus today's merge-target scan `_parent` (reversed ancestry to the first resident: one step when the parent is a resident, at most the full ancestry; unchanged), the pending-index meld (O(log n) in the melded requests, unchanged) and the root-heap re-offer (O(log R), unchanged) |
| `select` | stops at the first hit: O(position of the hit), O(S) on a full miss | none (moves only `_turn`) | the same: O(1) for a hit at `_turn`, O(position of the hit) otherwise, O(S) on a full miss with a small constant (two dict lookups per step); author candidate at or under baseline on every measured shape (E23) |
| `frontiers` | C copy of total live path length | none (pure) | C filter over total tip ancestry length (bounded by the same payload's deep copy) |
| `finish_recovery` | O(R·L + R²·L) | O(R·L) | O(R·L) |
| handoff sweep of R `recover(defer=False)` calls | O(R³·L) | O(R²·L) | O(R²·L) |

In the lookup bound, n is the number of `_up` entries, at most twice the
registered residents after every public operation (I-R4). The bound is the
one path compression gives alone, amortized O(log n) per lookup, not the
inverse-Ackermann bound, which needs union by rank or size as well; no
ranked union is added (adding one would be a mechanism the measured gain
does not need).

The quadratic term in exit and recovery is gone on every shape. No
rebuild, sort or prefix scan remains in `retire`, so no narrow-tree
regression is expected there; the measurement sweep must confirm it (Task 6),
and any shape slower than baseline beyond the control arm's band is a
Stop-if that reports to the owner. Author prototype, same shapes as above
(E14): `retire` 1.4 µs → 0.5 µs (one chain, depth 64), 14.2 µs → 1.2 µs
(depth 1,024), 28.0 ms → 0.7 µs (1,024 siblings); `finish_recovery`
13.7 ms → 0.23 ms (one chain, depth 1,024), 28.4 ms → 0.27 ms (1,024
siblings). These are a prototype subclass timed best-of-five; Task 6's
module-level sweep is the evidence.

### Files to modify

| File | Change | Task |
|---|---|---|
| `weft/core/family_scheduling.py` | `FamilyScheduler` state and the methods named in Task 4; module and class docstrings | 4 |
| `tests/core/test_family_scheduling.py` | append T1–T6 and T8–T15 (T7 is folded) | 3 |
| `docs/specifications/03-Manager_Architecture.md` | [MA-1.8] paragraph at line 445; mapping bullet after line 566; `## Related Plans` bullet | 5 |
| `docs/specifications/07-System_Invariants.md` | [MANAGER.18] paragraph at line 1139; `## Related Plans` bullet | 5 |
| `docs/program-theory.md` | [ALT-WT-016] lines 790–794 and 798, only if Owner Decision 1 accepts | 5 |
| `CHANGELOG.md` | `## [Unreleased]` → `### Changed` bullet | 5 |
| `docs/plans/README.md` | this plan's row to `completed` at closeout | 6 |

Not touched: `weft/core/manager.py`, `weft/core/task_state.py`, every other
test file. `git diff --stat "$BASE" -- weft/core/manager.py` must stay
empty.

### Read first

1. This plan, §1–§4.
2. `weft/core/family_scheduling.py` in full (431 lines).
3. `tests/core/test_family_scheduling.py` in full (479 lines).
4. `weft/core/manager.py` lines 1211–1225, 1645–1660, 2252–2305,
   2365–2425, 2522–2570, 2614–2630, 2757–2775, 5170–5200, 5660–5663.
5. `weft/core/runtime_identity.py` lines 237–247 (`submission_lineage`).
6. `docs/specifications/03-Manager_Architecture.md` lines 439–510.
7. `docs/program-theory.md` [ALT-WT-010] and [ALT-WT-016].

### Comprehension questions (answer in the execution log before the first edit)

A wrong answer means stop and re-read §3.

1. *Why does the ring hold exactly the childless residents?* Expected: today
   a stack is "a childless resident and its live ancestors" (`launched`
   extends the stack whose tip is the new task's nearest live ancestor or
   starts one; `retire` keeps only maximal paths, and a path is maximal
   exactly when its tip has no live descendant). A path is determined by its
   tip, so storing one entry per childless resident loses nothing; the path
   is derived on demand.
2. *Why does an exit with children touch no stack?* Expected: the stacks are
   the childless residents. When `tid` exits with `k ≥ 1` live children, no
   childless resident appears or disappears: `tid` was not childless, its
   children keep their own children, and its parent `p` gains `k` children
   and loses one, so `p` stays non-childless. Only the counts (`p += k - 1`)
   change; the children reach `p` through `_up[tid]` on their next lookup.
3. *Why must `_up` entries of dead nodes survive until compaction?*
   Expected: a live descendant's pointer may still name the dead node
   (compression happens on lookup, not on exit), and a pointer chain must
   never lead to a missing key. A dead entry can be dropped only when no
   entry points at it; compaction establishes that wholesale by rebuilding
   `_up` from residents alone, so every surviving pointer names a resident
   or `None`.
4. *What does `select`'s rotation guarantee?* Expected: each selection asks
   branches in ring order, starting after the branch that supplied the
   previous selection; a full miss leaves the order unchanged. While no stack
   is created or deleted, this is exactly today's cursor; a new stack is
   linked just before the cursor, so it is asked after every existing one; a
   contracting or extending stack keeps its place; a deleted stack is
   unlinked, and if it held the cursor the cursor passes to its successor. FIFO among independent families and the
   sibling fallback are unchanged.
5. *What does the checkpoint derivation cost, and why is that the floor?*
   Expected: one C-level filter over each tip's `ancestry_tids`, so the total
   ancestry length of the tips per call. The same payload deep-copies every
   resident's `ancestry_tids` list, which includes each tip's, so the
   derivation never exceeds the work of the write it feeds. Storing paths to
   make it cheaper would bring back the per-exit path maintenance this plan
   removes.
6. *Where does a launched child's scheduling parent come from, and why is
   the trailing scan bounded?* Expected: from `_nearest_live(ancestry)`, the
   first TID from the end of the request's ancestry that has an `_up` entry,
   resolved through `_find`, which stops only at a registered resident and
   follows the pointer of a former or deferred one. TIDs without an entry
   were never residents here, were compacted away, or are fresh deferred
   adoptions, so none is registered; the scan never costs more than today's
   `launched`, which filters the whole ancestry.

## 4. Invariants and Constraints

What must not change:

1. The [MA-1.8] selection contract: at a tip, the newest eligible child
   first (LIFO); the oldest independent pending family by original root ID
   (FIFO, `_oldest_independent` unchanged); otherwise an active family's
   oldest unassigned sibling, families taken round-robin; round-robin turns
   among active branches. [MANAGER.16] internal precedence lives in the
   Manager and is untouched.
2. The pending side, byte for byte: `_Head`, `_meld`, `_pop`,
   `_PendingIndex` (including `owner`, `merge`, `_owns`, `_compact`,
   `remove`, `candidate`), `_FamilyPending`, `PendingRequest`, `_Resident`,
   and the `FamilyScheduler` methods `pending`, `resident_tids`, `_parent`,
   `add_pending`, `remove_pending`, `_offer_root`, `_oldest_independent`,
   `release_background`, `recover`'s body. Tokens never move on exit
   ([ALT-WT-016]). `retire`'s pending merge is today's code unchanged: its
   target is `_parent(...)` for every resident, registered or deferred;
   only the frontier bookkeeping after it is new (invariant 8).
3. `frontiers`, compared as a set, equals the maximal live paths that
   today's `finish_recovery` builds from the same residents, whenever
   lineage is consistent and every launch registers a leaf (A1) (T1, T8).
   Its order is ring order.
4. The Manager checkpoint payload keeps its keys and the `stacks` shape (a
   list of lists of TIDs). `weft/core/manager.py` is not edited.
5. Every new structure is a Manager-local hint rebuilt from `_residents` by
   `finish_recovery`. Nothing new is published, and recovery still reads
   only `residents` ([WT-2a.2]; [WT-4] 4.1 "Queues carry facts; processes
   carry hints").
6. [ALT-WT-010] is not reopened. That entry refuses a general index or cache
   subsystem that becomes startup truth, and counters that exist only to
   claim constant cost. This plan replaces one in-memory representation of
   the same concept (the frontier stacks) with another inside the existing
   reducer; nothing becomes startup truth; the counts exist because the ring
   must know when a resident becomes childless, which today's code answers
   with a quadratic rebuild. If a reviewer reads the counts as a refused
   counter, both of the entry's reopening conditions hold: the cost is
   measured (E1, E2; backend-independent because the reducer does no I/O,
   and Task 6 runs the Manager suites on both backends), and the owner
   accepted the named design on 2026-11-03.
7. No new module, dependency, configuration knob, or public method. New
   private names only: `_up`, `_live_children`, `_ring_next`, `_ring_prev`,
   `_ring_tip`, `_tip_stack`, `_next_stack_id`, `_find`, `_live_parent`,
   `_nearest_live`, `_rotation_tips`, `_start_stack`, `_drop_stack`,
   `_contract`, `_compact_up` (`_turn` keeps its name with a new type).
8. Deferred adoption. Between `recover(..., defer=True)` and
   `finish_recovery()` a deferred resident is in `_residents` but is not
   registered: it has no count or stack, and no `_up` entry unless it is a
   former resident whose entry survived (not yet compacted). `_find` stops
   only at a registered resident, so it follows such a retained pointer
   past the deferred resident to the nearest registered ancestor, and a
   deferred resident is invisible to incremental operations whether or not
   it kept a pointer. During the deferred window the incremental structures
   are consistent with the registered set, not with `_residents`, and
   `finish_recovery` is the only operation that moves a resident from
   deferred to registered. Pending-token custody is not part of that
   registered view: buckets follow residents, deferred ones included,
   exactly as today ([MA-1.8] "late-discovered work rejoins at the original
   root FIFO position or extends a matching live tip"), so `add_pending`,
   `remove_pending` and `retire`'s merge use `_parent` throughout the
   window, while pointers, counts and the ring follow the registered set.
   `finish_recovery` reconciles the frontier bookkeeping and never the
   buckets, so a bucket merged into the wrong parent inside the window
   would stay there. The only caller that defers,
   `Manager._recover_scheduler_paths` (lines 2614–2629), calls
   `finish_recovery` right after its sweep, as does the one test that defers
   (`tests/core/test_family_scheduling.py` line 425). Calls in between are
   nevertheless defined, because today's reducer accepts them: `retire` of a
   deferred resident pops it, merges its pending bucket into `_parent(...)`
   as today, re-offers the root, and returns without touching pointers,
   counts or the ring (it cannot be in the ring; the pending
   `finish_recovery` rebuilds; T13); `launched` under a deferred parent
   treats the parent as unregistered (stepped over when it has no pointer,
   followed past by `_find` when it kept one), so the child attaches to its
   nearest registered ancestor, as a new stack or an in-place extension, or
   starts a root-level stack when there is none, and `finish_recovery`
   repairs it, exactly as in the handoff case of assumption A1 (T14); the
   exit of a registered resident beneath a deferred one contracts past the
   deferred one to the nearest registered ancestor while its bucket merges
   into the deferred one, as today (T13); `_compact_up`
   counts and rebuilds registered residents only, dropping a deferred
   resident's retained pointer with the other unregistered entries. The
   first draft raised `KeyError` in `_find` on such a `retire` where
   today's `retire` simply pops the resident (fresh-eyes correction 5,
   reproduced), and its `launched` skipped a deferred parent with no stated
   repair; the round-1 draft still raised `KeyError: '101'` when a former
   resident with a retained pointer was recovered with `defer=True` and a
   child was then launched beneath it or its child exited, because its
   `_find` stopped at any resident (cross-family round 1 F1; E25); and the
   round-1 fix also moved that exit's merge target to the registered
   ancestor, so a request beneath the exiting resident left the deferred
   resident's bucket and `select` after recovery chose differently
   (cross-family round 2; E26). All four were regressions, and this
   invariant with T13, T14 and T15 removes them.
9. Core invariants carried by every Manager change (TID immutability,
   forward-only state, reserved-queue policy, spawn-context processes,
   runtime-only `weft.state.*`) are untouched; the reducer does no I/O.

### Assumptions

Owner ruling, 2026-11-03, verbatim, after asking whether under the current
code a child could be registered before its parent and hearing the session
lead's answer (no, for the three reasons below): "record it as an
assumption that is true under the current code, with a note that if that
changes, the implementation might need to adjust."

- **A1. A newly launched task is a leaf: no living resident descends from it
  when `launched` registers it.** True under the current code for three
  reasons (each read in the live tree on 2026-11-03; `weft/` equals
  `b7eed9f9`, E7):
  1. Lineage is causal. A task is launched once, and its children can be
     submitted only by the task itself once it runs, so the parent process
     exists before any child request exists. The child's lineage record is
     built by the submitting process from its own runtime identity
     (`_submit_spawn_request_with_payload` in `weft/core/spawn_requests.py`,
     line 388, calls `submission_lineage`, `weft/core/runtime_identity.py`
     line 237, which appends the submitter's own TID to its ancestry when
     the submitter is finite), and the Manager validates the record at the
     submission boundary (`validate_submission_lineage`,
     `weft/core/runtime_identity.py` line 250, called from
     `runtime_identity_for_task` at line 282 and from
     `Manager._spawn_submitter_tid`, `weft/core/manager.py` line 5813). That
     validation checks the fields, the context, the TID form and
     acyclicity (`_validate_ancestry`, line 35); it does not compare the
     record with the submitter's recorded ancestry, so "a child's ancestry
     extends its submitter's" rests on the record being built by the
     submitter itself, not on a Manager-side comparison.
  2. `launched` runs only for this Manager's own launches, inside the launch
     commit (`Manager._commit_child_launch_success`, line 1672, calls
     `_register_launched_frontier` at line 1701, which calls `launched` at
     line 1653). A Manager never "launches" a task already running
     elsewhere, so it cannot register a parent after a child it also
     launched. By the time a child's request is read from the inbox, the
     parent's launch is committed: `Manager._process_queue_message` (line
     5170) refuses every spawn inbox while a launch is in flight
     (`_has_active_child_launches()` or `_child_launch_started_this_turn`,
     lines 5180–5186), a second guard, and refuses the ordinary inbox while
     any launch is undurable (`_scheduler_undurable`, line 5191; a launch
     whose result was lost is added at line 1293).
  3. Every other way a running task enters the scheduler is adoption
     (`recover`), and adoption rebuilds the whole frontier from every
     resident's recorded ancestry, never an incremental insert, both today
     and in the new design: snapshot handoff (`Manager._advance_launch_custody`,
     `recover` at line 2388) and reconciliation
     (`Manager._adopt_scheduler_evidence`, `recover` at line 2301) call
     `recover` with `defer=False`, which calls `finish_recovery`; the
     bootstrap sweep (`Manager._recover_scheduler_paths`) defers at line
     2622 and calls `finish_recovery` once at line 2629. The one ordering
     that looks like child-before-parent is a Manager handoff: Manager A
     launches the parent, the parent submits a child, A dies, and Manager B
     starts and launches the child before it has adopted the parent's
     task-state snapshot (snapshot publication is best-effort and can lag).
     B registers the child as a root-level path (no living ancestor known);
     when the parent's snapshot is adopted, `recover` rebuilds everything
     and the child's path becomes root-to-parent-to-child with the parent
     not a tip, the same result today's code produces (Task 3 T12).

  If a future change registers a running task incrementally outside the
  launch commit and the adoption rebuild, the derived paths could show a
  prefix pair where today's code shows two independent stacks; the
  implementation would then have to rebuild the frontier
  (`finish_recovery`) on that new registration path, because a late
  ancestor is bypassed by the descendant's parent pointer and child count as
  well as by the derived path (fresh-eyes correction 2 and FE-1: a different
  path derivation alone does not repair it; E16, E18). The `frontiers`
  docstring in Task 4 states the assumption in one sentence, where it bites.
  Task 2's reviewer confirms the three reasons hold at the baseline.

### Design decisions made while drafting

These settle points the design left open or correct it; the reviewer in
Task 2 should check each.

- D1. `_up` holds residents and former residents only; never-resident
  ancestors are not seeded. Seeding them would create chains of entries
  that no exit ever owns, so no retirement rule could drop them without
  stranding a pointer. `_nearest_live` steps over unknown trailing TIDs
  instead, which costs no more than today's `launched` filter and usually
  one step (a child normally launches while its submitter is live).
- D2. An exit with `k` children changes the parent's count by `k - 1`, not
  `k`: the parent loses the exiting child and gains its children.
- D3. `_up` retirement is wholesale compaction at `len(_up) > 2 *
  len(_live_children)` (twice the registered residents), not deletion at
  exit. Deleting a dead TID at a
  childless exit is unsafe: a dead descendant that exited with children may
  still point at it. The owner closed Owner Decision 3 on 2026-11-03 ("Yes,
  compact.").
- D4. `frontiers` uses `filter(residents.__contains__, ancestry)` (C speed)
  over the tip's full ancestry. Its cost is therefore total tip ancestry
  length, not live path length; that is still bounded by the deep copy of
  the same ancestry lists in the same payload. The `_up`-chain alternative
  is recorded, rejected, under "Alternatives considered" below.
- D5. The `select` equivalence with today's cursor holds while no stack is
  created or deleted between selections (in-place extension and contraction
  allowed), not merely "while no exit intervenes": a launch that starts a
  stack places it differently by design (linked just before `_turn`), and a
  deleted stack that held `_turn` passes it to its successor where today's
  `retire` re-sorts under an unadjusted index. T6 pins the equivalence; T11
  pins the two edit rules.
- D6. [MANAGER.18] receives the same sentence as [MA-1.8], because its third
  paragraph is a verbatim copy (E8); changing one copy would leave the
  invariant text contradicting the architecture text by omission.
- D7. `_parent` stays as is and stays the pending-bucket rule for
  `add_pending`, `remove_pending` and `retire`'s merge target, for every
  resident; `retire` uses `_find` only for the count and ring update of a
  registered resident (invariant 8; using `_find` for the merge as well
  moves pending custody inside a deferred window, cross-family round 2,
  E26).
- D8. The ring is a circular doubly linked list of stack ids with a cursor
  (`_turn`), not a `dict` in insertion order: §10 FE-2 and "Alternatives
  considered" below. Insertion at the tail (just before `_turn`), unlinking,
  and in-place tip replacement are each O(1); `select` moves only `_turn`.
- D9. A deferred adoption is a defined state, not a caller bug (invariant
  8): `retire` and `launched` handle it as today's reducer does, `_find`
  stops only at a registered resident (so a pointer the deferred resident
  kept as a former resident is followed past it), and `_compact_up` counts
  registered residents.

### Alternatives considered

| Alternative | Measured | Disposition |
|---|---|---|
| Ring as a plain `dict` in insertion order; each `select` pass iterates a snapshot `list(ring.items())` and moves every asked entry to the end (the first draft) | E19: hit at the ring head 0.33 µs today → 9.6 µs at S = 1,024 (about 30×); only hit at the far end 62.0 µs against 52.8 µs today. E23 (S = 1,024): head 9,201 ns against 319; far end 59,586 ns against 51,681 | Rejected: a per-dispatch regression of that size is a diminution (§10 FE-2). |
| Ring as a `collections.OrderedDict` (stack id → tip); each `select` step asks `next(iter(ring.items()))` and calls `ring.move_to_end(stack_id)` (the fresh-eyes reviewer's variant) | E19: head 0.33 µs; far end 93.7 µs against 62.0 µs for the plain dict and 52.8 µs today. E23 (S = 1,024): head 324 ns against 319; far end 95,150 ns against 51,681; full miss 237,852 ns against 147,279 | The measured alternative. Not the default because a far-end hit or a full miss costs 1.6–1.8× today; the owner may prefer it after Task 1 (§10 FE-2). |
| Circular doubly linked ring with a cursor (this plan, Task 4) | E23 (S = 1,024): head 261 ns against 319; far end 32,211 ns against 51,681; full miss 99,169 ns against 147,279 | Default. |
| `frontiers` derived by walking `_live_parent` from each tip instead of filtering the tip's ancestry | E14: 1.7–4.8× the filter's cost. E16: it reproduces today's frontiers after a late-ancestor launch | Rejected: it would not repair a bypassed ancestor, because the descendant's `_up` pointer and the counts bypass it as well (E18; §10 FE-1, closed). If assumption A1 ever fails, the named adjustment is a frontier rebuild (`finish_recovery`) at the new registration path. |

### Hidden couplings

- `Manager._family_candidate` caches one `select` result
  (`_peek_family_candidate`, line 5660); `select` advances the rotation when
  called, exactly as it advanced `_turn`. Clearing the cache
  (`_family_candidate = None` after launch, exit, recovery) is unchanged.
- Launch order and the leaf assumption: §4 "Assumptions" A1, with its
  three reasons and the adjustment a future change would need. In short:
  lineage is causal (a child's record is built by its running submitter);
  `launched` runs only at this Manager's own launch commit (manager.py line
  1701), behind the in-flight-launch inbox gate (lines 5180–5186) and the
  undurable-launch inbox gate (line 5191; `_retry_stale_child_launches`,
  line 1279, adds a lost launch at line 1293; the set is cleared by
  `_publish_scheduler_checkpoint` for TIDs with `host_processes` evidence,
  line 1508, and by `_ack_launch_custody`, line 2349); and adoption rebuilds
  the whole frontier. Were it to fail, the derived frontiers would contain a
  prefix pair (`("100", "101")` and `("100", "101", "102")`) where today
  they contain `("100", "101")` and `("100", "102")` (E16), and the
  descendant's `_up` pointer and the counts would bypass the late ancestor,
  so the descendant's pending bucket would merge into the grand-ancestor on
  its exit and the next `select` could differ from today's (E18).
- Lineage consistency. `_find` answers from the parent recorded at launch;
  `_parent` and `finish_recovery` read each TID's own ancestry. They agree
  when a child's ancestry extends its finite submitter's
  (`submission_lineage`), which the Manager validates at the boundary
  (`validate_submission_lineage`, `weft/core/runtime_identity.py` line 250;
  `_adopt_scheduler_evidence` raises on a changed identity, manager.py lines
  2269–2278). The property test generates lineage only through the
  `submission_lineage` rule and must not be widened to inconsistent lineage
  to find a divergence (Task 3 Stop-if).
- Background residents are always ring tips: a finite child of a background
  submitter inherits the submitter's ancestry without its TID, so the
  background TID is nobody's nearest live ancestor. `release_background` is
  therefore always a childless exit.
- `frontiers` is pure (it reads `_residents`, `_ring_tip`, `_ring_next` and
  `_turn` only; no compression), and it runs on every Manager task-state publication (§3), not
  only on scheduler checkpoints. The payload proxy in Task 6 measures that
  cost; it is the gate.
- `_compact_up` changes which TIDs have pointers, never which registered
  ancestor a lookup returns. `_PendingIndex` handles are a separate union-find and
  are not touched by it.
- `select` moves only `_turn`; it never relinks or reinserts a ring entry.
  `_next_stack_id` only grows; ids are never reused, so a stale id cannot
  alias a live stack.
- Four Manager contexts reach the changed code: positive-exit retirement
  (`retire`), startup-snapshot handoff and periodic reconciliation
  (`recover(defer=False)` → `finish_recovery`), and the bootstrap sweep
  (`recover(defer=True)` + `finish_recovery`). T1's generator exercises all
  four entry points.

### Error-path priorities

The reducer raises nothing new on any call sequence today's reducer
accepts, including calls inside a deferred window (invariant 8). A wrong
frontier, a stack missing from the ring, a broken ring link, or a pointer
chain that ends at a missing key is a correctness failure, fatal to the
change, never a best-effort outcome. Today's `ValueError` for a changed
resident identity is unchanged.

### Observable-difference register

Classes: A owner-decided removal; N no difference; C changed, no loss;
D diminution (must be fixed in plan or escalated).

| # | Observable | Before (`b7eed9f9`) | After | Class | Proof |
|---|-----------|---------------------|-------|-------|-------|
| 1 | Rotation order among branches | longest-first, re-sorted (stably) on every exit and recovery; `_turn` indexes the re-sorted list, so the next branch to be asked after an exit is arbitrary | a ring with a cursor: a new stack joins the rotation last, inserted immediately before the stack the cursor names next, so it is visited after every stack already waiting (not chronological creation order: create A, B, C, select A, create D gives B, C, A, D from the cursor, where creation order is B, C, D, A); a stack keeps its place when it extends or contracts; a stack whose tip exits beside a live sibling leaves; every recovery, including a mid-life `recover(defer=False)`, rebuilds the whole ring in `_residents` insertion order (each resident's launch or adoption order) and resets the rotation to the first of them, so a reconciliation can reorder running stacks, as today's rebuild reorders them by length (§10 FE-3, folded into Owner Decision 2) | C (owner ruling 2026-11-03; sentence added to [MA-1.8] and [MANAGER.18]; recovery wording under Owner Decision 2) | T2, T3, T10, T11 |
| 2 | `select` sequence while no stack is created or deleted | cursor `_turn` | the same sequence | N | T6; E13 (500 seeds against the real class) |
| 3 | `select` after a launch that starts a stack, or after a deletion | the new stack is asked after the stacks from `_turn` to the end, before the rest; a deletion re-sorts under an unadjusted index | the new stack is linked just before `_turn`, so it is asked after every existing stack; a deleted `_turn` passes to its successor | C (part of row 1) | T10, T11 |
| 4 | Exit cost (`retire`), by shape | quadratic in live stacks: 27 ms at 1,024 siblings; 11.0–11.2 µs one chain at depth 1,024; 26.7–51.2 µs for 4–16 chains (§3 tables) | frontier bookkeeping one amortized O(log n) lookup (path compression alone) plus O(1) updates; the whole call adds the unchanged merge-target scan, pending meld (O(log n)) and root re-offer (O(log R)); prototype 0.5–1.2 µs on every shape measured (E14) | C (improvement) | Task 6 sweep |
| 5 | Recovery cost (`finish_recovery`; a handoff sweep of R `recover(defer=False)`) | O(R²·L); sweep O(R³·L); 116 ms at 1,024 × 4 | O(R·L); sweep O(R²·L); prototype 0.01–0.46 ms on the measured shapes (E14) | C (improvement) | Task 6 sweep |
| 6 | Launch cost (`launched`) | O(full ancestry + S) | one amortized O(log n) lookup plus O(1) updates, plus the scan over trailing unknown ancestry | C (improvement) | Task 6 sweep (launch row) |
| 7 | Checkpoint `stacks` contents | the maximal live paths | the same paths as a set (consistent lineage) | N | T1, T8; Manager suites |
| 8 | Checkpoint `stacks` order | longest-first | ring order | C (row 1); no reader in `weft/` (§3, E10) | T2 |
| 9 | Checkpoint derivation (`frontiers`) and the payload it feeds | C copy of stored stacks: 0.4 µs to 126 µs on the prototype shapes | C filter over tip ancestry, tips walked from `_turn`: 1.4 µs to 180 µs for the first draft (at most 2.3 % of the payload proxy, E14); 1.5 µs to 157 µs for the linked ring, whose increase over today's copy on 1,024 siblings (95 µs in E24's run) is 4.1 % of E14's payload proxy for that shape, inside a 5 % band but close to it | N if Task 6 shows the payload proxy within the control band on every shape; otherwise D, and Task 6 stops and reports to the owner | Task 6 payload rows; E24 |
| 10 | Worst single `retire` latency | every call pays the rebuild | one whole-table compaction (R lookups) once more than half the pointers are dead (geometric along a draining chain), charged to the exits that created them; every other call one amortized O(log n) lookup plus the unchanged merge-target scan, meld and re-offer | C (Owner Decision 3 closed, "Yes, compact."); Task 6 reports the per-call maximum | Task 6 drain rows |
| 11 | Memory | `_stacks`: total live path length | `_up` ≤ 2 × registered residents entries (compacted wholesale past that, the owner's ruling), counts, the ring's three maps and the tip map, each O(residents); no stored paths | C (settled: Owner Decision 3 closed 2026-11-03, "Yes, compact.") | T9 |
| 12 | Pending side (buckets, meld, ownership, reclamation) | as is | unchanged | N | existing tests from line 227 of `tests/core/test_family_scheduling.py` |
| 13 | `_active_roots` cost per `_oldest_independent` | O(S) | O(S) | N | — |
| 14 | A launch that registers an ancestor of an existing resident | two independent stacks; the descendant's exit merges its bucket into the late ancestor | a prefix pair in the derived frontiers; the descendant's exit merges its bucket into the grand-ancestor, so selection can differ | N: unreachable under the current code (§4 assumption A1, owner ruling 2026-11-03); a future change that makes it reachable must add a frontier rebuild at its registration path | E16, E18, T12 |
| 15 | `select` cost per call | O(position of the first hit); 0.33 µs at S = 1,024 with a hit at the ring head (E19) | the same walk from a cursor over a linked ring: O(1) at the cursor, O(S) on a full miss; the author candidate at S = 1,024: head hit 261 ns against 319, far-end hit 32.2 µs against 51.7 µs, full miss 99.2 µs against 147.3 µs (E23) | C (expected parity on a head hit and on a full miss, to be measured; Task 6 gates both at or under baseline, Stop-if to the owner) | Task 1/6 `select` rows; E23 |
| 16 | Calls between `recover(..., defer=True)` and `finish_recovery`, including a deferred former resident whose `_up` entry survived | `retire` pops the resident and merges its bucket; `launched` filters the deferred parent into its path | the same outcomes once `finish_recovery` runs: `retire` of the deferred resident merges the bucket and leaves the frontier to the rebuild; `retire` beneath it contracts to the nearest registered ancestor and merges its bucket into the deferred resident, as today; `launched` attaches beneath the nearest registered ancestor until the rebuild repairs it; nothing raises | N | T13, T14, T15; E25, E26 |

No D row. Row 9 is N only if Task 6 confirms it; row 15 is C only if Task 6
confirms it; row 10 is reported with its measured maximum.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `b7eed9f9`: `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/07-System_Invariants.md`,
  `docs/specifications/05-Message_Flow_and_State.md`, and
  `docs/program-theory.md` at plan authoring time (2026-11-03; the working
  tree's uncommitted changes are other plan drafts and `docs/plans/README.md`
  only, so these files equal `b7eed9f9`, E7). Plan type: implementation with
  spec revision.
- Promotion baseline identifier: `ab24c416` (the landing commit of Tasks 4
  and 5, spec and code together under strategy B; 2026-11-03). The
  [ALT-WT-016] blocks are not part of it (Owner Decision 1 awaits the
  owner).

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/03-Manager_Architecture.md | B — atomic with the code | [MA-1.8] paragraph at line 445 (one sentence inserted); `_Implementation mapping_` bullet inserted after line 566; `## Related Plans` bullet before line 36 |
| docs/specifications/07-System_Invariants.md | B | [MANAGER.18] paragraph at line 1139 (the same sentence); `## Related Plans` bullet before line 1394 |
| docs/program-theory.md | owner-gated; lands in the Task 4–5 commit only if Owner Decision 1 accepts | [ALT-WT-016] "Current consequence" (lines 790–794) and "Evidence" (line 798) |

Why B and not A: the new sentence describes the ring. Under strategy A the
spec would promise the cursor-ring rotation while the shipped code re-sorts
longest-first. The delta is one sentence, one mapping bullet and two
backlinks; one atomic landing carries no reciprocity debt, and the new
mapping bullet names methods that exist in the same commit. [MF-5] (05 line
916) and [OBS.6] (07 line 388) describe `stacks` as "active frontier stacks"
with no order, so they need no block. [MANAGER.16] is unchanged (internal
precedence is in the Manager). The [MA-1.8] paragraph at line 502 already
says "Cache contracted scheduling-parent pointers; do not derive a full
graph per dispatch"; the design implements that sentence literally for
residents, so its text stays and the mapping bullet says so.

Clause audit: the [MA-1.8] and [MANAGER.18] replacements insert one sentence
after "Active branches receive round-robin turns and offer newest eligible
tip children first." and keep every other clause verbatim (the replacement
text is the current paragraph with the sentence inserted, built
mechanically, E8). The [ALT-WT-016] replacement keeps "Pending requests are
indexed incrementally once into `weft.state.labels.<root_tid>.pending.<parent_tid>`,
carrying immutable `ancestry_tids`" and "continues consuming
`.pending.<parent_tid>` directly as children are dispatched" verbatim;
"updates the bucket's contracted scheduling-parent pointer in $O(1)$ in
memory" becomes two clauses (the O(1) link and the O(log n) meld, both in
memory, no token moved); one sentence about frontier contraction is added.

### `docs/specifications/03-Manager_Architecture.md` [MA-1.8] — replace the paragraph that opens "Ordinary launch selection is work-conserving family scheduling" and closes "measure the same applicable cross-process resource population." (line 445)

Current text:

> Ordinary launch selection is work-conserving family scheduling over dynamically observed ancestry. There is no configured frontier count or family concurrency cap. Active branches receive round-robin turns and offer newest eligible tip children first. If none has an eligible tip child, the oldest independent pending family enters FIFO by original root ID; otherwise an active family may offer its oldest unassigned sibling subtree, choosing families round-robin. A live finite tip and its ancestors retain lineage, not a capacity permit. A child extends its matching ancestry path; a sibling starts another path when selected and when measured admission and existing launch custody allow it. Positive process exit contracts a path without dropping known descendants. Logical family membership remains while pending or resident descendants exist; late-discovered work rejoins at the original root FIFO position or extends a matching live tip. Queued requests incur no process or prospective connection charge. Multiple Managers retain exact-ID work stealing and measure the same applicable cross-process resource population.

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
Ordinary launch selection is work-conserving family scheduling over dynamically observed ancestry. There is no configured frontier count or family concurrency cap. Active branches receive round-robin turns and offer newest eligible tip children first. Active branches rotate in ring order from a cursor, and a new path joins the rotation last: it is inserted immediately before the path the cursor names next, so it is visited after every path already waiting; a path keeps its place when it extends to a child or contracts to its parent; a path that becomes a prefix of a sibling's path leaves the rotation; each selection asks branches in rotation order starting after the branch that supplied the previous selection; a recovery rebuild orders paths in resident adoption order and restarts the cursor at the first of them; no other order among branches is promised. If none has an eligible tip child, the oldest independent pending family enters FIFO by original root ID; otherwise an active family may offer its oldest unassigned sibling subtree, choosing families round-robin. A live finite tip and its ancestors retain lineage, not a capacity permit. A child extends its matching ancestry path; a sibling starts another path when selected and when measured admission and existing launch custody allow it. Positive process exit contracts a path without dropping known descendants. Logical family membership remains while pending or resident descendants exist; late-discovered work rejoins at the original root FIFO position or extends a matching live tip. Queued requests incur no process or prospective connection charge. Multiple Managers retain exact-ID work stealing and measure the same applicable cross-process resource population.
```

### `docs/specifications/07-System_Invariants.md` [MANAGER.18] — replace the paragraph that opens "Ordinary launch selection is work-conserving family scheduling" and closes "measure the same applicable cross-process resource population." (line 1139)

Current text:

> Ordinary launch selection is work-conserving family scheduling over dynamically observed ancestry. There is no configured frontier count or family concurrency cap. Active branches receive round-robin turns and offer newest eligible tip children first. If none has an eligible tip child, the oldest independent pending family enters FIFO by original root ID; otherwise an active family may offer its oldest unassigned sibling subtree, choosing families round-robin. A live finite tip and its ancestors retain lineage, not a capacity permit. A child extends its matching ancestry path; a sibling starts another path when selected and when measured admission and existing launch custody allow it. Positive process exit contracts a path without dropping known descendants. Logical family membership remains while pending or resident descendants exist; late-discovered work rejoins at the original root FIFO position or extends a matching live tip. Queued requests incur no process or prospective connection charge. Multiple Managers retain exact-ID work stealing and measure the same applicable cross-process resource population.

Replacement (`docs/specifications/07-System_Invariants.md`):

```text
Ordinary launch selection is work-conserving family scheduling over dynamically observed ancestry. There is no configured frontier count or family concurrency cap. Active branches receive round-robin turns and offer newest eligible tip children first. Active branches rotate in ring order from a cursor, and a new path joins the rotation last: it is inserted immediately before the path the cursor names next, so it is visited after every path already waiting; a path keeps its place when it extends to a child or contracts to its parent; a path that becomes a prefix of a sibling's path leaves the rotation; each selection asks branches in rotation order starting after the branch that supplied the previous selection; a recovery rebuild orders paths in resident adoption order and restarts the cursor at the first of them; no other order among branches is promised. If none has an eligible tip child, the oldest independent pending family enters FIFO by original root ID; otherwise an active family may offer its oldest unassigned sibling subtree, choosing families round-robin. A live finite tip and its ancestors retain lineage, not a capacity permit. A child extends its matching ancestry path; a sibling starts another path when selected and when measured admission and existing launch custody allow it. Positive process exit contracts a path without dropping known descendants. Logical family membership remains while pending or resident descendants exist; late-discovered work rejoins at the original root FIFO position or extends a matching live tip. Queued requests incur no process or prospective connection charge. Multiple Managers retain exact-ID work stealing and measure the same applicable cross-process resource population.
```

### `docs/specifications/03-Manager_Architecture.md` [MA-1.8] `_Implementation mapping_` — replace the bullet that opens "- [MA-1.8] Pending-index reclamation" (line 566) with itself followed by one new bullet

Current text:

> - [MA-1.8] Pending-index reclamation — `weft/core/family_scheduling.py::_PendingIndex.remove`, `_compact`, and `_owns` preserve bucket-local physical bounds and current ownership while retaining leftist meld.

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
- [MA-1.8] Pending-index reclamation — `weft/core/family_scheduling.py::_PendingIndex.remove`, `_compact`, and `_owns` preserve bucket-local physical bounds and current ownership while retaining leftist meld.
- [MA-1.8] Frontier ring and contracted ancestry — `weft/core/family_scheduling.py::FamilyScheduler` keeps one entry per childless resident in a circular linked ring with a cursor (`_ring_next`, `_ring_prev`, `_ring_tip`, `_tip_stack`, `_turn`), contracted scheduling-parent pointers over residents and former residents (`_up`; `_find` compresses on lookup, `_compact_up` drops former residents once they outnumber residents), and live-child counts (`_live_children`). `launched`, `retire`, and `finish_recovery` maintain them; `select` walks the ring from the cursor; `frontiers` derives each path from its tip's `ancestry_tids` for the Manager checkpoint. This realizes "Cache contracted scheduling-parent pointers" for residents. Verification: `tests/core/test_family_scheduling.py`.
```

### `docs/specifications/03-Manager_Architecture.md` `## Related Plans` — insert one bullet before the bullet that opens "- [Runtime ownership and test evidence repairs]" (line 36)

Current text:

> - [Runtime ownership and test evidence repairs](../plans/2026-10-27-runtime-and-test-repair-plan.md) describes command-lifecycle ownership, pending-index reclamation, and test-sensitivity repair design.

Replacement (`docs/specifications/03-Manager_Architecture.md`):

```text
- [Family Scheduler: Frontier Ring and Contracted Ancestry](../plans/2026-11-03-family-scheduler-frontier-ring-plan.md) replaces the quadratic frontier rebuild with a cursor ring over childless residents and contracted scheduling-parent pointers, and states the branch rotation order under [MA-1.8].

- [Runtime ownership and test evidence repairs](../plans/2026-10-27-runtime-and-test-repair-plan.md) describes command-lifecycle ownership, pending-index reclamation, and test-sensitivity repair design.
```

### `docs/specifications/07-System_Invariants.md` `## Related Plans` — insert one bullet before the bullet that opens "- [Runtime ownership and test evidence repairs]" (line 1394)

Current text:

> - [Runtime ownership and test evidence repairs](../plans/2026-10-27-runtime-and-test-repair-plan.md) describes command-lifecycle ownership, pending-index reclamation, and test-sensitivity repair design.

Replacement (`docs/specifications/07-System_Invariants.md`):

```text
- [Family Scheduler: Frontier Ring and Contracted Ancestry](../plans/2026-11-03-family-scheduler-frontier-ring-plan.md) replaces the quadratic frontier rebuild with a cursor ring over childless residents and contracted scheduling-parent pointers, and states the branch rotation order under [MANAGER.18].

- [Runtime ownership and test evidence repairs](../plans/2026-10-27-runtime-and-test-repair-plan.md) describes command-lifecycle ownership, pending-index reclamation, and test-sensitivity repair design.
```

### `docs/program-theory.md` [ALT-WT-016] — replace the "Current consequence" bullet (lines 790–794) — owner-gated (Owner Decision 1)

Current text:

> - Current consequence: Pending requests are indexed incrementally once into
>   `weft.state.labels.<root_tid>.pending.<parent_tid>`, carrying immutable
>   `ancestry_tids`. When `<parent_tid>` exits, the Manager updates the bucket's
>   contracted scheduling-parent pointer in $O(1)$ in memory and continues
>   consuming `.pending.<parent_tid>` directly as children are dispatched.

Replacement (`docs/program-theory.md`):

```text
- Current consequence: Pending requests are indexed incrementally once into
  `weft.state.labels.<root_tid>.pending.<parent_tid>`, carrying immutable
  `ancestry_tids`. When `<parent_tid>` exits, the Manager links the bucket to
  its contracted scheduling parent in $O(1)$ and melds the two in-memory
  pending indexes in $O(\log n)$, moving no token, and continues consuming
  `.pending.<parent_tid>` directly as children are dispatched. The live
  frontier contracts through the same contracted-pointer idea: an exit
  updates its own path and, when the pointer table has grown past twice the
  registered count, triggers one whole-table compaction charged to the exits
  that created the dead entries; each pointer lookup costs amortized
  $O(\log n)$ under path compression alone, and only the Manager checkpoint
  write remains proportional to the lineage it publishes.
```

### `docs/program-theory.md` [ALT-WT-016] — replace the "Evidence" line (line 798) — owner-gated (Owner Decision 1)

Current text:

> - Evidence: `2026-10-14-measured-admission-family-scheduling.md`.

Replacement (`docs/program-theory.md`):

```text
- Evidence: `2026-10-14-measured-admission-family-scheduling.md`;
  `2026-11-03-family-scheduler-frontier-ring-plan.md` (frontier contraction).
```

## 5. Tasks

Dependency order: 0 → 1 → 2 → 3 → 4 → 5 → 6. Tasks 4 and 5 land in **one
commit** (strategy B); nothing is committed between them. Commits are made
only when the owner asks. Run everything from the repository root after
`. ./.envrc`, with the `./.venv/bin/*` tools only.

### Task 0. Read, answer, record

- Outcome: the implementer can name the edit points and predict the ring
  after any exit.
- Do: read §3 "Read first"; write answers to the six comprehension questions
  under a dated "Implementer" heading in `## Execution log`. Record `BASE=$(git
  rev-parse HEAD)`.
- Verify: `./.venv/bin/python -m pytest tests/core/test_family_scheduling.py
  tests/core/test_work_conserving_admission.py -q -rA | grep -cE "^PASSED"`
  prints `35` and the same with `^FAILED` prints `0` (E6).
- Stop if: `BASE` does not descend from `b7eed9f9`, or `git diff --stat
  b7eed9f9 -- weft/core/family_scheduling.py weft/core/manager.py` is
  non-empty. Re-derive §3 line numbers and the baseline before continuing.
- Done when: the answers match §3's expected answers in substance.

### Task 1. Baseline measurement (no code change)

- Outcome: a recorded baseline at the tree as it is, in the execution log,
  that Task 6 compares against.
- Files to touch: none in the repository. Scripts live in the session
  scratchpad; record their full paths and outputs in the execution log.
- Method (reuse the replaced draft's E1, E2 and E9 methods, re-implemented
  if the old scratch scripts are gone):
  - E1/E2 shapes (S siblings at L = 1; S chains at L = 4): instruction
    counts with `sys.monitoring` (`PROFILER_ID`, INSTRUCTION events counted
    only where `code.co_filename == scheduling_mod.__file__`, `DISABLE` for
    other code objects, then `set_events(PROFILER_ID, 0)`, unregister,
    `free_tool_id`, `restart_events()`), and best-of-five `perf_counter_ns`
    per call, for `retire(tip)`, `finish_recovery()`, and `recover(new
    leaf)`.
  - The E9 sweep through the public reducer API: chain counts {1, 2, 4, 8,
    16, 32} × depths {64, 128, 256, 512, 1,024}, entries `retire` (the
    middle chain's tip) and `finish_recovery`; plus 9, 10 and 12 chains at
    depths 128, 512 and 1,024; plus the wide shapes (1,024 siblings at depth
    1 for `retire`; 1,024 branches at depth 4 for `finish_recovery`); plus
    the mixed shape (one chain of depth 1,024 plus 64, 512 or 1,000
    single-resident families).
  - New rows for this design: `launched` of a new leaf under a live tip and
    under a dead parent chain of depth 64; `frontiers` alone;
  - `select` rows (register row 15; §10 FE-2), one root `100` with S
    recovered leaf children, S ∈ {64, 256, 1,024}, 200 non-consuming
    `select` calls per timing, on three shapes: **head hit** (every tip holds
    three eligible children, so the first tip asked hits), **far-end hit**
    (only the last tip in rotation order holds children: S − 1 misses, then
    a hit), and **full miss** (no pending work: both passes walk all S tips,
    with `_oldest_independent` between them); plus the head-hit and
    full-miss shapes at each E9 chain count (one eligible child per chain
    tip, or none). Three structures, each an arm: the baseline cursor; the
    Task 4 linked ring (the Task 4 class compiled into a scratch module with
    lines 1–229 of the baseline file); and the OrderedDict variant of §4
    "Alternatives considered" (the same scratch module with
    `_ring_next`, `_ring_prev`, `_ring_tip` and `_turn` replaced by one
    `collections.OrderedDict[int, str]` (stack id → tip): a new stack is
    assigned at the end, a deleted one removed with `del`, a tip replaced by
    assignment in place, `frontiers` and `_active_roots` iterate its values,
    and each `select` pass runs `len(ring)` steps of `stack_id, tip =
    next(iter(ring.items()))` then `ring.move_to_end(stack_id)` before
    asking the tip). The two prototype arms are scratch files, not
    repository edits;
    and the **payload proxy**: `json.dumps({"stacks": [list(s) for s in
    scheduler.frontiers], "residents": copy.deepcopy(evidence)})`, where
    `evidence` maps each resident TID to the keys
    `Manager._checkpoint_admission_launch` writes (`context`, `root_tid`,
    `submitter_tid`, `ancestry_tids` as a list, `scheduling_class`,
    `connection_owner`, `hostname`, `host_processes` with one entry). The
    proxy is the reducer-visible part of `Manager._build_tid_state_payload`
    (lines 1211–1225).
  - Drain rows (for register row 10): build one chain of depth 1,024 and
    1,024 siblings, retire every tip one at a time until empty, and record
    the median and the maximum single-call `retire` time.
  - Arms: the imported module (baseline) and an unmodified copy compiled
    from the same source (control, the noise arm). Five rounds; each round
    shuffles every (arm, call) pair; report the range of round medians and
    the control/baseline band.
- Constraints: the reducer only; no Manager, broker or process. Do not edit
  `weft/core/family_scheduling.py` in this task.
- Stop if: the control/baseline band is wider than 0.95–1.05 in per-shape
  medians; fix the harness before Task 2 (the replaced draft found its
  subclass harness put the control at 1.12–1.18× and switched to compiling
  every arm from module source).
- Done when: every row above has a baseline range and the band is recorded.

### Task 2. Independent review of the plan and the delta (before any code)

- Outcome: a cross-family reviewer has read this plan, its `## Proposed
  Spec Delta`, and the code, and every finding has a disposition in
  `## Review Log`.
- Invocation: Tier-1 via `skills/call-agent/SKILL.md`, with the
  recommended prompt in `docs/agent-context/runbooks/writing-plans.md` §8 and
  the owner's one-scoped-round rule. A same-family subagent only as a logged
  Tier-2 fallback after two bounded Tier-1 attempts fail.
- Reviewer inputs: this plan; `weft/core/family_scheduling.py`;
  `weft/core/manager.py` at the §3 "Read first" ranges;
  `weft/core/runtime_identity.py` lines 237–264; the three spec files at
  `b7eed9f9`; `docs/program-theory.md` [ALT-WT-010], [ALT-WT-016], [WT-2a.2];
  `tests/core/test_family_scheduling.py`; Task 1's baseline numbers.
- Ask the reviewer specifically: design decisions D1–D9; confirm the three
  reasons of §4 assumption A1 hold at the baseline, including that neither
  clearing of `_scheduler_undurable` (`_publish_scheduler_checkpoint`,
  manager.py line 1508, and `_ack_launch_custody`, line 2349; fresh-eyes
  correction 3) can come before the TID's adoption; whether [ALT-WT-010] is
  reopened (§4 invariant 6); whether the compaction rule the owner chose
  (D3; Owner Decision 3, closed) is implemented soundly in Task 4; whether
  the deferred-window behavior (§4 invariant 8) matches today's.
- Stop if: the reviewer flags `RECONSIDERATION REQUESTED` against an
  `[ALT-WT-*]` entry, or finds a reachable path that launches a non-leaf
  (assumption A1 fails; the adjustment is a frontier rebuild at that
  registration path, a design change the owner sees). Re-classify and
  revise before Task 3.
- Done when: findings are dispositioned and Owner Decisions 1 and 2 and the
  FE-2 structure choice have answers (or the defaults are explicitly
  accepted); Owner Decision 3 is closed.

### Task 3. RED: write the tests (no production edit)

- Outcome: fourteen tests (T1–T6, T8–T15; T7 is folded into others)
  appended to `tests/core/test_family_scheduling.py`.
  Those marked RED fail at the baseline for the stated reason; those marked
  GUARD pass at the baseline and must keep passing. Each names the mutation
  of the new code that it must catch.
- Files to touch: `tests/core/test_family_scheduling.py` (append only; reuse
  its `pending` and `selected` helpers).
- Read first: the module's docstring ("The reducer boundary proves order and
  frontier custody without broker/process fakes").
- Constraints: real `FamilyScheduler`; no mocks, no monkeypatching of
  scheduler methods; `random.Random(seed)` only, never global randomness;
  internal attributes only where the mechanism itself is the subject (T4's
  `_find`, T5, T9, T12, and T15), as the existing
  `heap_ids` tests do.
- Shared helper (in the test module): `_reference_maximal_paths(residents)`,
  today's `finish_recovery` algorithm copied verbatim from `git show
  b7eed9f9:weft/core/family_scheduling.py` lines 423–430, returning a set of
  tuples. Its docstring cites `b7eed9f9` and says it changes only if the
  frontier contract changes on purpose.
- Tests:
  - **T1 GUARD** `test_frontier_tips_are_exactly_the_childless_residents_over_random_families`,
    parametrized over `seed in range(200)`, 400 steps per seed. The
    generator keeps its own table `tid -> (root_tid, ancestry_tids,
    scheduling_class)` and applies, at random: add a pending root, or a
    pending child of a node that has been launched (resident or exited;
    child ancestry per `submission_lineage`: append the parent's TID for a
    finite parent, inherit for a background parent); `select`, then
    `remove_pending` and `launched` with class `finite` (about 5/6) or
    `background`; `retire` of a random resident (interior ones included);
    `release_background` of a random resident; non-deferred `recover` of a
    launched, exited finite node; a batch of up to three deferred `recover`
    calls followed by `finish_recovery`. After every step assert, through
    the public surface and the test's own table: (a) the tips of
    `frontiers` are distinct and equal the childless residents (a resident
    is childless when no other resident's nearest live ancestor, found by
    scanning that resident's ancestry from the end, is it); (b)
    `set(frontiers) == _reference_maximal_paths(...)` over the current
    residents. Mutations that must fail it: delete the stack when the parent
    becomes childless (instead of contracting); contract when the parent
    still has another child (instead of deleting); `_live_children[p] -= 1`
    instead of `+= k - 1`. The author's prototype tripped each of these by
    seed 2 (E15).
  - **T2 RED** `test_contracted_stack_keeps_its_rotation_position`.
    `launched` 300; 301 (child of 300); 100; 101 (child of 100); 102
    (child of 101); 200. Assert `frontiers == (("300", "301"), ("100",
    "101", "102"), ("200",))`. `retire("301")`. Assert `frontiers ==
    (("300",), ("100", "101", "102"), ("200",))`. Baseline fails:
    `(("100", "101", "102"), ("300",), ("200",))` (E17). Mutation: delete
    and re-append the contracted stack gives `(("100", "101", "102"),
    ("200",), ("300",))`.
  - **T3 RED** `test_exit_beside_a_live_sibling_drops_the_stack_and_keeps_order`.
    `launched` 100; 101 and 102 (children of 100); 200; 201 (child of 200);
    202 (child of 201). `retire("101")`. Assert `frontiers == (("100",
    "102"), ("200", "201", "202"))`. Baseline fails: `(("200", "201",
    "202"), ("100", "102"))` (E17). Mutation: contracting instead of
    deleting leaves a third stack `("100",)`.
  - **T4 RED (AttributeError at baseline)**
    `test_interior_exit_leaves_ring_unchanged_and_children_contract_to_ancestor`.
    `launched` 100; 101 (child of 100); 102 and 103 (children of 101); 200.
    `retire("101")`: assert `frontiers == (("100", "102"), ("100", "103"),
    ("200",))` and `scheduler._find("101") == "100"`. `retire("102")`:
    `frontiers == (("100", "103"), ("200",))`. `retire("103")`: `frontiers ==
    (("100",), ("200",))`. These frontier values equal the baseline's (E17);
    the test is RED at the baseline only through `_find`. Mutation:
    `_live_children[p] -= 1` instead of `+= k - 1` makes 100 never become a
    tip, so the last assertion sees `(("200",),)`.
  - **T5 RED (AttributeError)** `test_contracted_pointer_lookup_compresses_dead_chain`.
    Recover ten unrelated roots `"900"`–`"909"` (so compaction does not fire),
    then `launched` 100, 101 (child of 100), 102 (child of 101), 103 (child
    of 102), 104 (child of 103). Retire 103, then 102, then 101 (each
    interior, bottom up, so each exit's own lookup stops at its still-live
    parent and compresses nothing). Assert `scheduler._up["103"] == "102"`
    and `scheduler._up["102"] == "101"` (a dead chain of three, 15 pointers
    for 12 residents, so no compaction); call `scheduler._find("103")` and
    assert it returns `"100"`; then assert `scheduler._up["103"] ==
    scheduler._up["102"] == "100"`. Retiring top down instead would compress
    during the exits themselves and leave nothing to test. Mutation: no
    compression keeps `_up["103"] == "102"` (still correct, but linear; this
    test proves the amortized claim's mechanism; E17 `EV/candidate/t5_check.py`
    prints `before 102 101 15 12`, `find 100`, `after 100 100 100`).
  - **T6 GUARD** `test_select_rotation_matches_cursor_arithmetic_while_stacks_are_fixed`,
    parametrized over `seed in range(50)`. The linked ring has a cursor like
    the baseline, so while stacks are fixed the chosen-tip sequence must be
    the baseline cursor's exactly; the edit rules for insertions and
    deletions, where the baseline differs by design (register rows 1 and
    3), are pinned by T11 against the same cursor arithmetic. Recover roots `"100"`, `"200"`,
    …, `"800"`; give each 0–3 pending children (seeded). Snapshot the tips in
    `frontiers` order as a list `T` and set `turn = 0`. Repeat until
    `select()` returns `None`: compute the expected candidate with today's
    arithmetic (for `offset` in `range(len(T))`: `i = (turn + offset) %
    len(T)`; if the test's model has a pending child for `T[i]`, expect its
    newest and set `turn = (i + 1) % len(T)`), then `remove_pending` the
    actual result (no launch, so stacks stay fixed) and assert equality.
    During the first 40 selections, with seeded probability 1/2, add one
    new pending child under a random root (and to the model), so a tip that
    missed earlier can hit later (the fresh-eyes pending-work injection).
    Without these additions every missed tip stays empty and a rotation
    mutant can pass every seed: against the first draft's dict ring, the
    "rotate only the chosen entry" mutant passed all 50 seeds without them
    and failed 42 of 50 with them (E20). Against the linked ring, the
    mutants "set `_turn` to the successor of the starting stack instead of
    the chosen one" and "never advance `_turn`" each fail 50 of 50 seeds
    with them (E22).
    A second case covers the sibling pass: recover 100 and 101 (child of
    100), 200 and 201 (child of 200), so the tips are 101 and 201; add three
    pending children under `("100",)` and three under `("200",)`. No tip has
    a pending child and no independent family waits, so every selection
    comes from the sibling pass; with `T = [101, 201]` the model expects
    family 100's oldest, then family 200's oldest, alternating. Passes at
    baseline (same cursor). Mutation: the sibling pass never advances
    `_turn`.
  - **T7** is folded into T1 (b) and T2/T3 (the set equality with the
    reference algorithm, and the rotation rule); no separate test.
  - **T8 GUARD** `test_recovery_rebuilds_the_same_frontiers_from_residents`.
    Build a family by launches and exits (a fixed script: two roots, a chain
    of depth 4, a fan-out of 3, two interior exits). Record `frontiers` and
    the residents with their ancestry. Build a second scheduler with
    `recover(tid, root, ancestry, defer=True)` for each resident in the
    first scheduler's residents, in `sorted(first.resident_tids)` order, then
    `finish_recovery()`. Assert equal sets of frontiers and equal stack
    counts, and that the second scheduler's ring order is its childless
    residents in recovery order. Mutation: build the ring from all residents
    instead of childless ones.
  - **T9 RED (AttributeError)** `test_contracted_pointers_stay_bounded_by_live_residents`.
    Run T1's generator (shared as a module-level helper) for seeds 0–19 and
    assert after every step: `len(scheduler._up) <= 2 *
    len(scheduler.resident_tids)`, and for every resident `t`,
    `scheduler._live_parent(t)` equals the nearest live ancestor found by
    scanning `t`'s own ancestry from the end (the `_find`/`_parent`
    agreement of §4 invariant 2), and I-R5 (every key of
    `scheduler._ring_tip` has `_ring_prev[_ring_next[k]] == k`; walking
    `_ring_next` from `_turn` visits every key once before returning; `_turn
    is None` exactly when `_ring_tip` is empty). Mutations: disable
    `_compact_up` (E15: fails at seed 0); a `_find` that returns the first
    dead node instead of the first live one; a `_drop_stack` that forgets to
    relink the predecessor.
  - **T10 RED** `test_new_stack_joins_rotation_after_existing_stacks`.
    Recover roots 100 and 200, each with pending children 101–103 and
    201–203. `select()` → 103 (not removed). `launched` root 300; add pending
    301–303 under it. Then three times `select()` and `remove_pending`.
    Assert the sequence after the first selection is `[203, 103, 303]`.
    Baseline gives `[203, 303, 103]` (E17). Mutation: insert new stacks at
    the ring head.
  - **T11 RED** `test_select_rotation_matches_cursor_model_across_stack_edits`,
    parametrized over `seed in range(50)`. The test keeps a cursor model: a
    list `T` of tips and an index `c`, edited only by these rules, which are
    today's cursor arithmetic plus the rotation sentence of the spec delta:
    a new stack is inserted before the cursor (`T.insert(c, x); c += 1`, or
    `T = [x]; c = 0` when `T` is empty); a deleted stack at index `i` is
    removed (`T.pop(i)`; `c -= 1` if `i < c`; `c = 0` if `c == len(T)`); an
    extension or contraction replaces `T[i]` in place. Script (seeded):
    `launched` six roots, give some 0–2 pending children (the test's table
    records each child's parent and each node's live children), then 120
    steps. Each step: if no tip in `T` has pending work, add a pending child
    under a random tip (or `launched` a new root when `T` is empty) and
    continue; otherwise compute the expected
    candidate from `T` and `c` exactly as T6 does (newest pending child of
    the first tip from `c` that has one; `c` moves past it), call
    `select()` and assert equality, `remove_pending` it, then at random:
    `launched` it under its tip (extension in place; sometimes add a
    pending child under it); `launched` a new root (insertion) with a
    pending child; `retire` a random tip (its pending children move to its
    parent in the table; the stack contracts in place when the parent is
    left childless, else it is deleted; a retired root's stack is deleted);
    add a pending child under a random tip. After every step assert
    `[f[-1] for f in scheduler.frontiers] == T[c:] + T[:c]`. Baseline fails
    on 50 of 50 seeds (new stacks at list index `n`; re-sort on exit).
    Mutations: link a new stack at the head (`_turn` = the new stack) fails
    50 of 50; a deleted `_turn` passing to its predecessor fails 40 of 50
    (E22).
  - **T12 RED (AttributeError at baseline; its frontier assertions pass at
    baseline)** `test_adoption_after_child_launch_rebuilds_the_parent_path`.
    The handoff case of §4 assumption A1. On a fresh scheduler, `launched`
    the child `PendingRequest(101, "100", "100", ("100",))`, whose ancestry
    names a parent `100` that is not a resident. Assert `frontiers ==
    (("101",),)`, then `len(scheduler._ring_tip) == 1`, the ring's only tip
    is `"101"`, and `scheduler._live_parent("101") is None` (the child is a
    root-level stack; `_find("101")` itself would return `"101"`, because
    `_find` of a resident is the resident, so the parent pointer is read
    through `_live_parent`). Then `scheduler.recover("100", "100", ())`
    (`defer=False`). Assert `frontiers == (("100", "101"),)`, `"100" not in
    scheduler._tip_stack` (the parent is not a tip), `len(scheduler._ring_tip)
    == 1`, and `scheduler._live_parent("101") == "100"`. At baseline both
    `frontiers` assertions pass (today's `recover(defer=False)` calls
    `finish_recovery`, which rebuilds, so the old `_stacks` already show the
    single path) and the first internal assertion raises `AttributeError`;
    it is therefore RED only in the T4 sense and guards behavior the
    baseline already has. Mutation it catches: an adoption that inserts
    incrementally instead of rebuilding (the parent becomes a second,
    root-level stack and the frontier holds the prefix pair `(("100",
    "101"), ("100",))`, E22).
  - **T13 GUARD** `test_retire_of_a_deferred_resident_merges_its_bucket_and_leaves_the_ring`.
    `recover("100", "100", ())`; `recover("101", "100", ("100",),
    defer=True)`; `add_pending` request 150 with ancestry `("100",)` and
    request 160 with ancestry `("100", "101")` (its bucket is 101's, because
    101 is a resident). `retire("101")` must not raise; assert `frontiers ==
    (("100",),)`; `finish_recovery()`; assert `frontiers == (("100",),)` and
    `select().request_id == 160` (160's bucket merged into 100's, and the
    newest child at the tip is chosen). Second case, the retained-pointer
    case (cross-family round 1 F1): `launched` 100, 101 (child of 100) and
    102 (child of 101); `retire("101")` (interior, so its `_up` entry is
    retained); `recover("101", "100", ("100",), defer=True)`;
    `retire("102")` must not raise; assert `frontiers == (("100",),)` (the
    stack contracted to 100, the nearest registered ancestor);
    `finish_recovery()`; assert `frontiers == (("100", "101"),)` (the single
    rebuilt path). Third case, the bucket merge beneath a deferred resident
    (cross-family round 2): the second case's setup (`launched` 100, 101,
    102; `retire("101")`; `recover("101", "100", ("100",), defer=True)`);
    `add_pending` request 150 with ancestry `("100", "101")` (its bucket is
    the deferred 101's) and request 160 with ancestry `("100", "101",
    "102")` (its bucket is 102's); `retire("102")`; `finish_recovery()`;
    assert `frontiers == (("100", "101"),)` and `select().request_id ==
    160` (102's bucket merged into the deferred 101, the nearest resident,
    so 101's bucket holds both and the newest is chosen). All three cases
    pass at baseline. Mutations: the first draft's `retire` without the
    deferred branch raises `KeyError: '101'` on the first case; a `retire`
    that pops a deferred resident but skips its pending merge selects 150
    on the first case (E22); a `_find` that stops at any resident (`not in
    self._residents`, as in the first draft and the round-1 draft) instead
    of at a registered one raises `KeyError: '101'` on the second case
    (E25); merging the bucket into the registered ancestor `_find` returns
    instead of into `_parent`'s nearest resident (the round-1b candidate)
    selects 150 on the third case (E26).
  - **T14 GUARD** `test_launch_under_a_deferred_parent_is_repaired_by_finish_recovery`.
    `recover("100", "100", (), defer=True)`; `launched`
    `PendingRequest(101, "100", "100", ("100",))` must not raise;
    `finish_recovery()`; assert `frontiers == (("100", "101"),)`;
    `retire("101")`; assert `frontiers == (("100",),)` (the rebuilt count
    made 100 childless, so the stack contracts to it). Passes at baseline.
    Mutations: a `_nearest_live` that tests `_residents` membership instead
    of `_up` membership returns the deferred 100 and raises `KeyError:
    '100'` on its count; a `finish_recovery` that does not rebuild leaves
    `frontiers == ()` after the exit (E22).
  - **T15 RED in the T4 sense (AttributeError at baseline; the behavior it
    pins is the baseline's)**
    `test_launch_under_a_deferred_parent_with_a_retained_pointer_attaches_to_the_registered_ancestor`.
    The retained-pointer case (cross-family round 1 F1), split out of T14
    so that T14 stays a GUARD under this task's stop condition (its
    internal assertion raises `AttributeError` at baseline). `launched`
    100, 101 (child of 100) and 102 (child of 101); `retire("101")`;
    `recover("101", "100", ("100",), defer=True)`; `launched`
    `PendingRequest(103, "100", "101", ("100", "101"))` must not raise;
    assert `scheduler._live_parent("103") == "100"` (the child attached to
    the nearest registered ancestor; `frontiers` cannot show this, because
    it filters by `_residents`); `finish_recovery()`; assert `frontiers ==
    (("100", "101", "102"), ("100", "101", "103"))` and
    `scheduler._live_parent("103") == "101"`. At baseline the launch does
    not raise and the rebuilt frontiers are these (E25), but the first
    `_live_parent` assertion raises `AttributeError`, so the test is RED
    only in the T4 sense and guards behavior the baseline already has.
    Mutation: a `_find` that stops at any resident instead of at a
    registered one raises `KeyError: '101'` on the launch (E25).
- Red proof: run the file; paste each RED test's failing assertion line (or
  `AttributeError` line) into the execution log, and the GUARD tests'
  passing lines.
- Stop if: a GUARD test fails at the baseline (the oracle is wrong; fix the
  test, not the expectation), or the T1 generator needs lineage that
  `submission_lineage` cannot produce to find a divergence (report it; do
  not widen the contract).
- Done when: the RED/GUARD split matches this list exactly.

### Task 4. GREEN: the ring (core work; same commit as Task 5)

- Outcome: `FamilyScheduler` uses the ring; T1–T6, T8–T15 and every
  existing test in `tests/core/test_family_scheduling.py` pass.
- Files to touch: `weft/core/family_scheduling.py` only.
- Edit: replace `class FamilyScheduler` (line 230 to the end of the file)
  with the code below. Lines 1–229 do not change. This code was written as a
  prototype module copy (E22, which supersedes E17's dict-ring candidate;
  E25 re-ran every check after the round-1 `_find` fix, and E26 after the
  round-2 merge-target fix):
  `ruff format --check`, `ruff check` (C901 limit 10; the largest are
  `select` at 7 and `launched` at 6) and `mypy` pass on it under
  `pyproject.toml`, and the existing 24 tests pass against it unchanged.

```python
class FamilyScheduler:
    """Work-conserving FIFO family entry, round-robin branches and LIFO descent.

    Spec: [MA-1.8]. Only the caller can declare a resident positively exited.
    No method performs broker I/O or infers death from a missing observation.

    Frontier stacks are the childless registered residents; a resident is
    registered when it has a ``_live_children`` count, which every resident
    has except a deferred adoption awaiting ``finish_recovery``. The ring is
    a circular doubly linked list of stack ids (``_ring_next``,
    ``_ring_prev``) with one tip per stack (``_ring_tip``); ``_turn`` is the
    stack asked first. A stack's path is derived from its tip's ancestry,
    never stored. ``_up`` is a union-find over residents and former
    residents: each entry points at an ancestor with no registered resident
    in between, and lookups compress chains of unregistered nodes (the
    contracted scheduling-parent pointer). ``_live_children`` counts the
    registered residents whose nearest registered ancestor is the key. All
    of these are hints rebuilt by ``finish_recovery``.
    """

    def __init__(self) -> None:
        self._pending: dict[int, PendingRequest] = {}
        self._parents: dict[str, _PendingIndex] = {}
        self._family_pending: dict[str, _FamilyPending] = {}
        self._request_indexes: dict[int, _PendingIndex] = {}
        self._family_counts: dict[str, int] = {}
        self._roots: list[tuple[int, str]] = []
        self._indexed_roots: set[str] = set()
        self._residents: dict[str, _Resident] = {}
        self._up: dict[str, str | None] = {}
        self._live_children: dict[str, int] = {}
        self._ring_next: dict[int, int] = {}
        self._ring_prev: dict[int, int] = {}
        self._ring_tip: dict[int, str] = {}
        self._tip_stack: dict[str, int] = {}
        self._turn: int | None = None
        self._next_stack_id = 0

    @property
    def frontiers(self) -> tuple[tuple[str, ...], ...]:
        """Return checkpoint stacks in rotation order; derived, never stored.

        Filtering a tip's ancestry yields its path only because every launch
        registers a leaf; a registration that adds a live ancestor of an
        existing resident must rebuild through ``finish_recovery``.
        """
        residents = self._residents
        live = residents.__contains__
        return tuple(
            (*filter(live, residents[tip].ancestry_tids), tip)
            for tip in self._rotation_tips()
        )

    @property
    def resident_tids(self) -> frozenset[str]:
        return frozenset(self._residents)

    def pending(self, request_id: int) -> PendingRequest | None:
        return self._pending.get(request_id)

    def _parent(self, root_tid: str, ancestry_tids: tuple[str, ...]) -> str:
        return next(
            (tid for tid in reversed(ancestry_tids) if tid in self._residents), root_tid
        )

    def _find(self, tid: str | None) -> str | None:
        """Return the nearest registered resident at or above ``tid``; compress.

        Only a resident with a live-child count stops the walk. A former
        resident and a deferred adoption with a retained pointer are both
        followed past, so lookups see the registered set until
        ``finish_recovery`` registers the deferred residents.

        Spec: [MA-1.8] ("Cache contracted scheduling-parent pointers").
        """
        registered = self._live_children
        root = tid
        while root is not None and root not in registered:
            root = self._up[root]
        while tid is not None and tid not in registered:
            self._up[tid], tid = root, self._up[tid]
        return root

    def _live_parent(self, tid: str) -> str | None:
        parent = self._find(self._up[tid])
        self._up[tid] = parent
        return parent

    def _nearest_live(self, ancestry_tids: tuple[str, ...]) -> str | None:
        for tid in reversed(ancestry_tids):
            if tid in self._up:
                return self._find(tid)
        return None

    def _rotation_tips(self) -> list[str]:
        """Return the tips in rotation order, starting at ``_turn``."""
        tips: list[str] = []
        stack_id = self._turn
        if stack_id is not None:
            for _ in range(len(self._ring_tip)):
                tips.append(self._ring_tip[stack_id])
                stack_id = self._ring_next[stack_id]
        return tips

    def _start_stack(self, tip: str) -> None:
        """Link a new stack at the ring's tail, just before ``_turn``."""
        stack_id = self._next_stack_id
        self._next_stack_id += 1
        self._ring_tip[stack_id] = tip
        self._tip_stack[tip] = stack_id
        head = self._turn
        if head is None:
            self._ring_next[stack_id] = self._ring_prev[stack_id] = stack_id
            self._turn = stack_id
            return
        tail = self._ring_prev[head]
        self._ring_next[tail] = self._ring_prev[head] = stack_id
        self._ring_next[stack_id] = head
        self._ring_prev[stack_id] = tail

    def _drop_stack(self, stack_id: int) -> None:
        """Unlink a stack; ``_turn`` moves to its successor if it was the head."""
        del self._ring_tip[stack_id]
        following = self._ring_next.pop(stack_id)
        preceding = self._ring_prev.pop(stack_id)
        if following == stack_id:
            self._turn = None
            return
        self._ring_next[preceding] = following
        self._ring_prev[following] = preceding
        if self._turn == stack_id:
            self._turn = following

    def _contract(self, tid: str, live_parent: str | None, children: int) -> None:
        """Hand an exited resident's children to its parent and fix its stack."""
        if live_parent is not None:
            self._live_children[live_parent] += children - 1
        stack_id = self._tip_stack.pop(tid, None)
        if stack_id is not None:
            # A childless exit contracts its stack in place or, when a sibling
            # path still holds the parent, drops the stack.
            if live_parent is not None and not self._live_children[live_parent]:
                self._ring_tip[stack_id] = live_parent
                self._tip_stack[live_parent] = stack_id
            else:
                self._drop_stack(stack_id)
        self._compact_up()

    def _compact_up(self) -> None:
        """Drop former residents once they outnumber residents (cf. ``_compact``)."""
        if len(self._up) > 2 * len(self._live_children):
            self._up = {tid: self._live_parent(tid) for tid in self._live_children}

    def add_pending(self, request: PendingRequest) -> None:
        """Index accepted work once; a repeated projection is idempotent."""
        if request.request_id in self._pending:
            return
        self._pending[request.request_id] = request
        parent = self._parent(request.root_tid, request.ancestry_tids)
        index = self._parents.setdefault(parent, _PendingIndex())
        index.add(request.request_id)
        self._request_indexes[request.request_id] = index
        self._family_pending.setdefault(request.root_tid, _FamilyPending()).add(
            request.request_id
        )
        count = self._family_counts.get(request.root_tid, 0)
        self._family_counts[request.root_tid] = count + 1
        if count == 0:
            self._offer_root(request.root_tid)

    def remove_pending(self, request_id: int) -> PendingRequest | None:
        """Forget exact reserved/absent work and bound bucket-local history [MA-1.8]."""
        request = self._pending.pop(request_id, None)
        if request is not None:
            self._family_counts[request.root_tid] -= 1
            index = self._request_indexes.pop(request_id).owner()
            index.remove(request_id, self._pending, self._request_indexes)
            if not index.count:
                parent = self._parent(request.root_tid, request.ancestry_tids)
                if self._parents.get(parent) is index:
                    self._parents.pop(parent)
            self._family_pending[request.root_tid].remove(request_id)
            if not self._family_counts[request.root_tid]:
                self._family_pending.pop(request.root_tid)
                self._family_counts.pop(request.root_tid)
        return request

    def _offer_root(self, root_tid: str) -> None:
        if root_tid not in self._indexed_roots:
            heapq.heappush(self._roots, (int(root_tid), root_tid))
            self._indexed_roots.add(root_tid)

    def _active_roots(self) -> set[str]:
        return {self._residents[tip].root_tid for tip in self._ring_tip.values()}

    def _oldest_independent(self) -> PendingRequest | None:
        active = self._active_roots()
        parked: list[tuple[int, str]] = []
        candidate = None
        while self._roots:
            key, root = heapq.heappop(self._roots)
            self._indexed_roots.discard(root)
            if not self._family_counts.get(root):
                continue
            parked.append((key, root))
            if root not in active:
                candidate = self._family_pending[root].candidate(self._pending)
                break
        for _, root in parked:
            self._offer_root(root)
        return candidate

    def select(self) -> PendingRequest | None:
        """Choose one candidate without consuming work or reserving a place.

        Each pass walks the ring from ``_turn``; a hit moves ``_turn`` to the
        successor of the stack that supplied it, and a miss leaves it unchanged.
        """
        start = self._turn
        if start is None:
            return self._oldest_independent()
        ring_next, ring_tip = self._ring_next, self._ring_tip
        stack_id = start
        for _ in range(len(ring_tip)):
            bucket = self._parents.get(ring_tip[stack_id])
            candidate = (
                bucket.candidate(self._pending, self._request_indexes)
                if bucket
                else None
            )
            if candidate is not None:
                self._turn = ring_next[stack_id]
                return candidate
            stack_id = ring_next[stack_id]
        candidate = self._oldest_independent()
        if candidate is not None:
            return candidate
        # Live paths supply sibling candidates when no independent family waits.
        for _ in range(len(ring_tip)):
            family = self._family_pending.get(
                self._residents[ring_tip[stack_id]].root_tid
            )
            candidate = family.candidate(self._pending) if family else None
            if candidate is not None:
                self._turn = ring_next[stack_id]
                return candidate
            stack_id = ring_next[stack_id]
        return None

    def launched(
        self, request: PendingRequest, scheduling_class: SchedulingClass = "finite"
    ) -> None:
        """Register a successful launch; background retains its startup lineage."""
        if scheduling_class == "internal":
            return
        tid = str(request.request_id)
        resident = _Resident(request.root_tid, request.ancestry_tids, scheduling_class)
        existing = self._residents.get(tid)
        if existing is not None:
            if existing != resident:
                raise ValueError("resident scheduling identity changed")
            return
        # A deferred adoption is not registered: the scan steps over it when it
        # has no pointer, and _find follows its retained pointer past it when it
        # has one; finish_recovery repairs the path either way.
        parent = self._nearest_live(request.ancestry_tids)
        self._residents[tid] = resident
        self._up[tid] = parent
        self._live_children[tid] = 0
        if parent is None:
            self._start_stack(tid)
            return
        self._live_children[parent] += 1
        stack_id = self._tip_stack.pop(parent, None)
        if stack_id is None:
            self._start_stack(tid)
        else:
            self._ring_tip[stack_id] = tid
            self._tip_stack[tid] = stack_id

    def release_background(self, tid: str) -> None:
        """Release a validated background task after its startup gate clears."""
        resident = self._residents.get(tid)
        if resident is not None and resident.scheduling_class == "background":
            self.retire(tid)

    def retire(self, tid: str) -> None:
        """Contract a positively exited resident without touching pending tokens.

        Pending custody follows residents, deferred ones included: the bucket
        merges into ``_parent`` exactly as before. Frontier bookkeeping follows
        the registered set: counts and ring contract through ``_find``, and a
        deferred adoption, in no stack yet, is left to ``finish_recovery``.
        """
        resident = self._residents.pop(tid, None)
        if resident is None:
            return
        parent = self._parent(resident.root_tid, resident.ancestry_tids)
        source = self._parents.pop(tid, None) if parent != tid else None
        if source is not None:
            self._parents.setdefault(parent, _PendingIndex()).merge(source)
        children = self._live_children.pop(tid, None)
        if children is not None:
            self._contract(tid, self._find(tid), children)
        if self._family_counts.get(resident.root_tid):
            self._offer_root(resident.root_tid)

    def recover(
        self,
        tid: str,
        root_tid: str,
        ancestry_tids: tuple[str, ...],
        scheduling_class: SchedulingClass = "finite",
        *,
        defer: bool = False,
    ) -> None:
        """Adopt live/unknown finite evidence; bootstrap may defer path rebuilding.

        Until ``finish_recovery`` runs, a deferred resident has no count or
        stack, and any pointer it kept as a former resident is followed past:
        ``retire`` merges its pending bucket and leaves the frontier to the
        rebuild, and lookups resolve to its nearest registered ancestor.
        """
        if scheduling_class != "finite":
            return
        self._residents[tid] = _Resident(root_tid, ancestry_tids, scheduling_class)
        if not defer:
            self.finish_recovery()

    def finish_recovery(self) -> None:
        """Rebuild pointers, counts and the ring once after an unordered sweep."""
        residents = self._residents
        self._up = {
            tid: next(
                (item for item in reversed(r.ancestry_tids) if item in residents), None
            )
            for tid, r in residents.items()
        }
        self._live_children = dict.fromkeys(residents, 0)
        for parent in self._up.values():
            if parent is not None:
                self._live_children[parent] += 1
        self._ring_next, self._ring_prev = {}, {}
        self._ring_tip, self._tip_stack = {}, {}
        self._turn = None
        for tid, children in self._live_children.items():
            if not children:
                self._start_stack(tid)
```

- Style: keep `from __future__ import annotations`, the import block, full
  annotations, and `Spec:` references. The class docstring above replaces
  today's; the module docstring keeps its three spec references.
- Not allowed: storing paths; a helper class or module for the ring; a
  root-count map (unless Task 1 or 6 shows `_active_roots` dominating a
  measured shape, recorded in the execution log); seeding `_up` with
  never-resident TIDs; a `_find` (or any other lookup) that stops at an
  unregistered resident, i.e. tests `_residents` where the code tests
  `_live_children`; changing any method listed in §4 invariant 2; a
  configuration knob or tuning constant other than the `2 *` of
  `_compact_up`, which mirrors `_PendingIndex.remove`; a `select` that
  snapshots, moves, or relinks ring entries (it moves only `_turn`).
- Verify: `./.venv/bin/python -m pytest tests/core/test_family_scheduling.py
  tests/core/test_work_conserving_admission.py -q` passes. Run each named
  mutation from Task 3 once (edit, run, record the failing test, restore;
  never commit a mutant).
- Stop if: T1 fails on any seed (the design or §4 is wrong; return with the
  seed and the diverging frontiers), or any existing test needs an edit.

### Task 5. Spec, theory, CHANGELOG, mapping, backlinks (same commit as Task 4)

- Outcome: the spec states the rotation order, the mapping names the new
  owner, the theory edit lands if accepted, and the CHANGELOG records the
  change.
- Files to touch: `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/07-System_Invariants.md`, `docs/program-theory.md`
  (only if Owner Decision 1 accepts), `CHANGELOG.md`.
- Apply every Replacement block under `## Proposed Spec Delta` exactly, with
  the wording the owner chose under Owner Decisions 1 and 2. If Owner
  Decision 1 declines the theory edit, skip both `docs/program-theory.md`
  blocks; nothing else depends on them.
- CHANGELOG: under `## [Unreleased]`, in the existing `### Changed` list,
  add:
  `- The Manager's family scheduler keeps its live frontier as a ring over childless residents with contracted scheduling-parent pointers (union-find), so a task exit and a recovery no longer rebuild and re-sort every frontier path. Branches now take turns in a ring with a cursor instead of longest first: a new branch joins the rotation after every branch already waiting, and a branch keeps its place as its path extends or contracts; the \`stacks\` list in the Manager checkpoint follows that order. No compatibility shim: nothing reads that order back.`
  (write the backticks around `stacks` unescaped).
- Record the landing commit SHA as the promotion baseline identifier in
  `## Spec Baseline` once the owner asks for the commit.
- Verify: `./bin/check-doc-paths`;
  `./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py -q`;
  `grep -c "Frontier Ring and Contracted Ancestry" docs/specifications/03-Manager_Architecture.md docs/specifications/07-System_Invariants.md`
  prints `1` for each file (the backlink; the mapping bullet uses a different
  title).
- Stop if: the paragraph at 03 line 445 and 07 line 1139 are no longer
  byte-identical (someone edited one; reconcile before applying).

### Task 6. Measure after, full gates, traceability, closeout

- Outcome: the acceptance numbers, green gates on both backends, the [WT-7]
  closeout, the completed-work review, and the index flip.
- Measurement: re-run every Task 1 row with three arms: the edited module
  (the "ring" arm, now importable), and baseline and control both compiled
  from `git show b7eed9f9:weft/core/family_scheduling.py`. Acceptance:
  - `retire`, `finish_recovery`, `launched`: every shape at or under
    baseline within the control band (per-shape medians);
  - wide shapes (1,024 siblings `retire`; 1,024 × 4 `finish_recovery`) at
    least 100× faster;
  - payload proxy: every shape within the control band of baseline;
  - drain rows: report the median and the maximum single `retire` per
    shape next to the baseline's; the maximum is reported, not gated;
  - `frontiers` alone: reported, not gated (it is a component of the payload
    proxy);
  - `select`: the head-hit and full-miss shapes at every S and every E9
    chain count at or under baseline within the control band (per-shape
    medians); the far-end-hit rows and the OrderedDict arm are reported, not
    gated;
- Stop if: any gated row is slower than baseline beyond the band. Do not
  tune; return to the owner with the numbers (register row 9 or row 15
  becomes D; for `select`, the owner may then choose the OrderedDict
  variant, §10 FE-2).
- Gates (§7): the per-task tests, the Manager-level suites on SQLite and
  PostgreSQL, the full suite, `mypy`, `ruff`, `./bin/check-doc-paths`, and
  the empty `git diff --stat "$BASE" -- weft/core/manager.py`.
- Traceability: the `Spec: [MA-1.8]` line in `_find`'s docstring; the new
  mapping bullet; the backlinks; the class docstring. Check with
  `grep -n "MA-1.8" weft/core/family_scheduling.py`.
- [WT-7] closeout (class-5 completion): the owner writes the possession
  probe key; a non-authoring agent of a different family answers from
  `docs/program-theory.md` alone; record key and result in `## Review Log`.
  Refresh the theory-versus-code audit table row for 4.2-a in
  `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (line 5450)
  only if the owner asks; otherwise record its new status here.
- Completed-work review: a cross-family review of the diff per
  `skills/call-agent/SKILL.md`, one scoped round.
- Closeout: flip this plan's `Status:` and its `docs/plans/README.md` row to
  `completed` in the same change.

- [WT-7] keyed possession probe (2026-11-04). Key, items P1, C1, B3 from the fourteen-item bank the owner reviewed on 2026-11-02 (held outside the repository; items reusable by the owner's instruction): P1 "Retry a failed task up to N times with backoff" (expected: above Weft by re-submission; nearest primitive the reserved-queue keep/clear disposition [WT-2a.7]; an in-Manager retry counter makes Weft a workflow engine [WT-5]); C1 "Cache task status in a Manager-local table so `weft task list` is fast" (expected refusal: [WT-2a.2], [ALT-WT-010], [ALT-WT-019]; correct move an observer-addressed read model over queues or faster peeks); B3 "Audit the family scheduler's retire path" (expected: frontier structures rebuilt on retire instead of the O(1) consume-on-dispatch update [ALT-WT-016]). Taker: codex 0.161.0, read-only sandbox, a different family from the plan author and the bank drafter; its command log shows reads of `docs/program-theory.md` and nothing else. Grading rule as in plan 1 (pass on reaching the expected placement, refusal or prediction with at least one expected entry or one that entails it); graded by the series lead against the owner-reviewed bank. The owner's instruction of 2026-11-04: "Do all the probe keys. You can re-use them." Result: PASS, three of three. (1) above Weft as application policy through a coordinating task, a new identity per attempt; nearest primitive the reserved queue's keep (task-level REQUEUE removed); cited [WT-5], [WT-2], [WT-2a.7], [ALT-WT-006], [WT-2a.1], [WT-2a.3]. (2) refused as process-local state becoming status truth; cited [WT-2a.2], [ALT-WT-010], [ALT-WT-020], "Queues carry facts; processes carry hints" ([ALT-WT-019] not cited but entailed); correct move the newest-valid snapshot reader over queues. (3) repeated global work on retirement, scanning or rebuilding the frontier graph or moving pending children, quadratic under an exit burst, against pointer contraction and amortized compaction; cited [ALT-WT-016], [WT-7]. The probe is spent for this plan; `completed` still waits on the owner's ruling on register row 9 (the 1.043 checkpoint payload proxy on the 1,024-sibling shape) and the 4.2-a audit row that follows it, so the plan stays `promoted`.

## 6. Testing Plan

- Harness: the reducer tests use a bare `FamilyScheduler`, as the existing
  module does ("The reducer boundary proves order and frontier custody
  without broker/process fakes"). The Manager-level proof uses the existing
  real-Manager suites with real queues and, where they launch, real child
  processes.
- What proves the change: T2, T3 and T10 pin the new rotation order (the
  [MA-1.8] sentence); T1 pins that the ring is exactly the childless
  residents and that the derived frontiers equal the reference algorithm on
  every step of 200 random families, through all four entry points; T6 pins
  that the rotation is today's cursor while stacks are fixed, and T11 pins
  the cursor's edit rules across insertions and deletions; T4, T5 and T9
  pin the pointer mechanics (contraction of children, compression, bounded
  memory, ring links); T8 pins that recovery rebuilds the same frontiers;
  T12 pins the handoff case of assumption A1; T13, T14 and T15 pin the
  deferred window (§4 invariant 8), including a deferred former resident
  with a retained pointer and the pending-bucket merge beneath it.
- Existing tests that must stay green unedited: all of
  `tests/core/test_family_scheduling.py` (24 items at `b7eed9f9`; the
  pending-side tests from line 227 protect §4 invariant 2), and the
  Manager-level targets: 
  `tests/core/test_admission_recovery.py::test_positive_scoped_retirement_acknowledges_exact_request_then_releases_checkpoint`
  and `::test_retirement_requires_scoped_whole_process_exit` (exit-path
  custody; they do not reach the frontier code, so they guard only the exit
  path around it);
  `tests/core/test_manager_family_maintenance.py::test_missing_launch_result_retains_finite_path_and_late_commit_is_idempotent`
  (real process; reconciliation → `recover(defer=False)`);
  `tests/core/test_manager_family_maintenance.py::test_positive_startup_snapshot_wakes_admission_without_maintenance_tick`
  (startup handoff; asserts `frontiers`);
  `tests/core/test_family_source_reconciliation.py::test_invalid_primary_settles_without_using_frontier_or_launching_valid_work`;
  `tests/core/test_family_dispatch_scaling.py::test_completion_to_next_real_launch_does_not_scan_sibling_backlog`
  (broker-call oracle);
  `tests/core/test_work_conserving_admission.py::test_manager_offers_sixth_independent_family`
  and `::test_manager_records_more_than_five_live_paths`;
  `tests/system/test_family_matrix.py::test_postspawn_no_pid_checkpoint_preserves_unknown_frontier_and_stalls_new_root`
  (checkpoint `stacks` after crash recovery, single stack);
  `tests/system/test_family_scheduling.py::test_entire_finite_family_and_its_leaves_overlap_beyond_five`
  (a root that exits while its children live; `tests/helpers/family_workload.py`
  drives it).
- Do not mock: the Manager, broker queues, `WeftTestHarness`, process launch,
  snapshots, or any `FamilyScheduler` method.
- Not tested, by decision: a wall-time assertion in the suite (timing is
  Task 1/6 evidence, not a test; the instruction-count growth tests of the
  replaced draft guarded a helper that no longer exists, and every
  remaining operation is either an amortized O(log n) pointer lookup or a
  single pass, which T5 and T9 cover
  mechanically).

## 7. Verification

```bash
. ./.envrc
# per task
./.venv/bin/python -m pytest tests/core/test_family_scheduling.py tests/core/test_work_conserving_admission.py -q
# Manager-level, SQLite
./.venv/bin/python -m pytest tests/core/test_admission_recovery.py tests/core/test_manager_family_maintenance.py tests/core/test_family_source_reconciliation.py tests/core/test_family_dispatch_scaling.py tests/core/test_work_conserving_admission.py tests/system/test_family_matrix.py tests/system/test_family_scheduling.py tests/system/test_family_recovery.py -q
# Manager-level, PostgreSQL (Docker)
./bin/pytest-pg tests/core/test_family_scheduling.py tests/core/test_admission_recovery.py tests/core/test_manager_family_maintenance.py tests/core/test_family_source_reconciliation.py tests/core/test_family_dispatch_scaling.py tests/core/test_work_conserving_admission.py tests/system/test_family_matrix.py tests/system/test_family_scheduling.py tests/system/test_family_recovery.py tests/system/test_measured_admission_pg.py
# spec and plan gates
./bin/check-doc-paths
./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py -q
# final gates
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft/core/family_scheduling.py tests/core/test_family_scheduling.py
git diff --stat "$BASE" -- weft/core/manager.py   # must print nothing
```

Success: T1–T6 and T8–T15 behave as Task 3 states before Task 4 and all pass after
it; every listed suite is green on both backends; the full suite, `mypy`,
`ruff` and `./bin/check-doc-paths` pass; `weft/core/manager.py` is
unchanged; Task 6's gated rows meet their acceptance.

Runtime observation (optional evidence, not a gate): in `weft manager serve`
logs, the existing `scheduler_checkpoint` timing
(`Manager._timed_manager_call`, line 1503) on a wide fan-out family should
fall, because the exit batch no longer rebuilds frontiers before the
checkpoint.

## 8. Rollback

Stated before the tasks run. The change is one commit (Tasks 4–5); `git
revert` restores the prior state. Nothing durable changes shape: the
checkpoint `stacks` list keeps its type and contents, recovery reads only
`residents`, and no queue, payload key or configuration is added. Mixed
versions on one broker (an old and a new Manager during an upgrade): each
Manager rebuilds its own frontiers from residents, and neither reads the
other's `stacks`, so the only difference between them is rotation order.
No rollout order. One-way doors: none (the theory edit and the spec
sentence are text, reversible by revert).

## 9. Out of Scope

- The pending side (§4 invariant 2), including `_PendingIndex._compact`.
- The O(S) passes in `select` and `_active_roots`; they are dispatch-side
  costs [MA-1.8] already bounds by broker operations.
- The checkpoint's whole-state republish per exit batch and its deep copy of
  `residents` (`Manager._build_tid_state_payload`).
- Any change to `weft/core/manager.py`, the checkpoint payload, or lineage
  validation.
- Inconsistent (malformed) lineage beyond what the Manager's validation
  already rejects.
- The theory-versus-code audit table edit, unless the owner asks (Task 6).

## 10. Owner Decisions

1. **[ALT-WT-016] wording** (carried from the replaced draft's fork F2).
   Proposed block under `## Proposed Spec Delta` (two blocks: "Current
   consequence" and "Evidence"). Default: accept. It separates the bucket
   link (O(1)), the meld (O(log n)), and frontier contraction (amortized
   O(log n) per lookup under path compression alone, with no ranked union
   added (adding one would be a mechanism the measured gain does not need);
   an exit updates its own path and, when the pointer table has grown past
   twice the registered count, triggers one whole-table compaction charged
   to the exits that created the dead entries; the checkpoint write stays
   proportional to the lineage it publishes). Declining leaves the theory
   as is; the plan proceeds.
   Status 2026-11-03: awaiting the owner. The implementer applied both
   blocks, confirmed `./bin/check-doc-paths` passes with them, saved the
   diff outside the repository and reverted it; the theory commit lands only
   on the owner's acceptance.
2. **The rotation-order sentence** for [MA-1.8] and [MANAGER.18]. Proposed
   text in the two blocks. Default: accept as written. The sentence names
   the four cases (new path, extension, contraction, prefix collapse), the
   cursor rule, and recovery order, and says no other order is promised.
   The plan's proposed wording of the rule (cross-family round 1 F3, which
   found "the order their paths were created" imprecise): "a new path joins
   the rotation last: it is inserted immediately before the path the cursor
   names next, so it is visited after every path already waiting; a path
   keeps its place when it extends or contracts; a recovery rebuild orders
   paths in resident adoption order and restarts the cursor at the first of
   them". It is not chronological creation order: after creating A, B, C,
   selecting A (the cursor moves to B) and creating D, the rotation from the
   cursor is B, C, A, D, where chronological cyclic order would be B, C, D,
   A. The algorithm is unchanged; only the description is.
   Recovery order (fresh-eyes FE-3, folded here on 2026-11-03): a recovery
   rebuild orders paths in resident adoption order (the order in which
   `_residents` received each resident, by launch or by adoption) and
   resets the rotation to the first of them; a mid-life
   `recover(defer=False)` (reconciliation, snapshot handoff) therefore
   reorders running stacks, as today's rebuild reorders them by length
   (register row 1, class C; E21). The proposed sentence says so ("recovery
   rebuilds the rotation in resident adoption order and restarts it at the
   first rebuilt path"); the wording is the owner's, and "in the order the
   residents were registered" is an equivalent alternative.
   A shorter alternative the owner may prefer: "Active branches rotate in
   ring order from a cursor; a new path joins the rotation last, and a path
   keeps its place when it extends to a child or contracts to its parent."
   (it leaves the insertion point, the cursor rule and recovery order
   implicit).
   Closed on the owner's implement instruction (2026-11-03) with the
   recommended option: accept as written (the long sentence, including the
   recovery wording), applied to [MA-1.8] and [MANAGER.18].
3. **`_up` retirement rule — closed 2026-11-03.** Owner, after the
   compaction rule was explained in full: "Yes, compact." The table of
   parent pointers (`_up`) is compacted wholesale, rebuilt from the living
   (registered) residents, when its size exceeds twice their number: the
   trigger the pending index already uses (`physical_count > 2 * count` in
   `_PendingIndex.remove`, line 162, which then calls
   `_PendingIndex._compact`); D3, register rows 10 and 11, Task 4
   `_compact_up`. History, one sentence each: deleting a pointer at its
   TID's exit was rejected because a dead descendant's pointer could be
   stranded (the D3 cross-check in the execution log reproduces the
   `KeyError`); keeping every pointer until the next `finish_recovery` was
   rejected because memory would grow by one entry for every TID ever
   resident in the Manager's life, a diminution against today's bounded
   `_stacks`.

Fresh-eyes findings (2026-11-03) and their dispositions:

- **FE-1 (O1 remedy) — closed 2026-11-03, absorbed into §4 assumption A1.**
  E18 showed that a non-leaf launch also misdirects the descendant's
  pending merge and the next `select` (baseline 104, first-draft ring 103),
  so a different path derivation alone would not repair it. A1 records
  that, if the assumption ever fails, the adjustment is a frontier rebuild
  (`finish_recovery`) at the new registration path; the `_up`-chain
  derivation is rejected under §4 "Alternatives considered".
- **FE-2 (`select` cost) — fixed in the design 2026-11-03; one owner
  choice remains.** The first draft's `select` snapshotted the whole ring
  (`list(self._ring.items())`) on every call, so a hit at the ring head
  cost about 30× today's (0.33 µs → 9.6 µs at S = 1,024, E19). A
  per-dispatch regression of that size is a diminution and is not
  accepted, and the snapshot is not kept. The ring is now a circular doubly
  linked ring with a cursor (§3 "The design"; D8; Task 4), which moves only
  `_turn`: the author candidate is at or under baseline on every measured
  shape (E23), and Task 6 gates `select` (register row 15). Owner decision
  for FE-2: the linked ring is the plan's default; the owner may prefer the
  OrderedDict variant (§4 "Alternatives considered"; E19 and E23 measure it
  at baseline on a head hit and 1.6–1.8× baseline on a far-end hit or full
  miss) after Task 1's numbers. If the OrderedDict variant still shows the
  measured full-miss regression after Task 1, choosing it would be a
  diminution: it would need `RECONSIDERATION REQUESTED` under the
  no-diminution rule ([WT-4] 4.4 "No capability diminution") and an
  explicit owner decision, not a silent pass of the Task 6 gate
  (cross-family round 1 observation).
  Closed on the owner's implement instruction (2026-11-03) with the
  recommended option: the circular linked ring with a cursor. Task 1's
  numbers meet the plan's criterion for keeping it (the linked prototype
  at 0.60–0.93 of baseline on every `select` row; the OrderedDict variant
  1.30–1.92 on far-end hits and full misses); Task 6 confirms it on the
  real module (0.62–0.93).
- **FE-3 (recovery order wording) — folded into Owner Decision 2 on
  2026-11-03.** A recovery rebuild orders paths in resident adoption order
  and resets the rotation to the first of them, so a mid-life
  `recover(defer=False)` reorders running stacks (E21; register row 1,
  class C). The proposed sentence now says so; the wording stays the
  owner's.

Open items (questions reading could not settle; not owner decisions unless
Task 2 makes them so). The former O1 ("can a launch register an ancestor of
an existing resident?") is no longer open: the owner ruled it a named
assumption (§4 "Assumptions", A1), with a stated consequence and a guard
test (T12).

- **O2. Does the payload proxy stay within the control band?** The first
  draft's derivation was at most 2.3 % of the proxy (E14). The linked
  ring's walk from `_turn` adds a Python loop: on 1,024 siblings its
  increase over today's copy was 95 µs in E24's run, 4.1 % of E14's
  payload proxy for that shape, inside a 5 % band but close to it. Only
  Task 6 decides (register row 9).
  Decided by Task 6's measurements (2026-11-03), as the owner's implement
  instruction directs: **no** on one shape. The proxy is within the
  control band on 1x64, 1x1024, 4x512 and 32x64 (0.998–1.004) and 4.3 %
  above baseline on 1,024 siblings (1.043, per-round 1.023–1.064, against
  the control's 1.000, per-round 0.975–1.016). Register row 9 is therefore
  D on that shape; under the Task 6 Stop-if this is reported to the owner
  untuned (execution log, Task 6).

## Review Log

- 2026-11-03: this file replaces
  `2026-11-02-family-scheduler-incremental-retire-plan.md` under the owner's
  2026-11-03 ruling (§2). What this redraft closes: the old draft's fork F1
  (keep longest-first order; closed by "I don't think that we make
  guarantees about launch order anywhere" and the accepted ring design),
  its fork F3 (the residual latency shapes of the rent-or-buy helper, which
  no longer exists), and its helper `_maximal_paths` with the trie and
  switch history. Its fork F2 continues as Owner Decision 1. Its two
  cross-family rounds (codex 0.161.0, 2026-11-02) reviewed the helper, not
  this design; their findings on Manager-level proof scope (round 1 F2:
  `tests/core/test_admission_recovery.py` does not reach frontier code) are
  carried into §6.
- self-driven fresh-eyes review: done 2026-11-03 (distinct pass after drafting): 12 corrections — select cost claim and complexity row corrected (FE-2, register row 15 D, Task 1/6 select rows); O1 consequences extended beyond `frontiers` and remedy corrected (FE-1, E18, row 14); O1 serialization evidence adds the `_scheduler_undurable` gate; invariants 2 and 3 qualified by leaf-only launches; invariant 8 names the real `KeyError` site; register row 1 recovery order made precise (FE-3); register row 5 range corrected to 0.01–0.46 ms; T6 adds pending children so the rotate-only-chosen mutant fails (E20); Task 3 test count nine, not ten; T8 recovery order made explicit; class docstring line 234; execution log notes the `cand_checks.py` second argument and that E12–E21 rest on scratch scripts.
- owner rulings and fresh-eyes dispositions applied 2026-11-03 after the fresh-eyes pass: owner decision 3 closed ('Yes, compact': wholesale compaction of the parent-pointer table past twice the live count); open item O1 recorded as a named assumption true under the current code (lineage is causal; `launched` runs only at this Manager's own launch commit behind the in-flight-launch and undurable-launch inbox gates; adoption rebuilds the whole frontier), with the adjustment a future change would need (a frontier rebuild at the new registration path) and one guard test for the handoff case; FE-1 absorbed into that assumption; FE-2 (the 30× head-hit `select` regression of the snapshotting dict ring) fixed by a circular linked ring with a cursor, the OrderedDict variant recorded as the measured alternative and the snapshot rejected; invariant 8's deferred-resident `KeyError` fixed with two RED tests; FE-3 folded into owner decision 2's wording. Cross-family round 1 pending. (Applied as: the two deferred-window tests T13 and T14 are labelled GUARD in Task 3 because they pass at the baseline, and fail against the first draft; T11 is the new RED rotation test; the handoff guard is T12; evidence E22–E24.)
- Cross-family review round 1 (codex 0.161.0, 2026-11-03, whole redraft
  after the fresh-eyes pass and the owner's rulings): BLOCKED on F1 (P2, a
  former resident with a retained parent pointer recovered with
  `defer=True` made `_find` stop at an unregistered resident, so a launch
  beneath it or an exit of its child raised `KeyError` where the baseline
  accepts both): accepted, `_find` now stops only at a registered resident
  and follows the pointer of any unregistered node, deferred or dead, with
  T13/T14 extended by the retained-pointer case. F2 (P2, inverse-Ackermann
  claimed without ranked union; whole-method costs conflated with frontier
  bookkeeping): accepted, amortized O(log n) stated, the two cost columns
  separated, the compaction caveat added. F3 (P2, 'creation order' does not
  describe insertion before a moving cursor): accepted as the plan's
  proposed wording under owner decision 2. The E2 instruction counts and
  the eight deterministic test recipes reproduced exactly in the round.
  Round 2 (codex 0.161.0, 2026-11-03, scoped to F1–F3,
  FE-2 note confirmed): F2 and F3 closed (the embedded code reproduces B,
  C, A, D; [MANAGER.18] promises round-robin without a chronological
  order). F1 returned one residual inside its applied span, applied here
  without a further round under the one-scoped-round rule: the
  registered-ancestor lookup had also moved the pending-bucket merge
  target (the reviewer's sequence selected 150 where the baseline selects
  160); `retire` now merges the bucket into today's resident-based parent
  and contracts the ring through the registered ancestor, with the
  sequence added to T13 and the retained-pointer launch case split into
  its own RED test. Review loop on the redraft closed; the plan waits on
  the owner's decision 1 ([ALT-WT-016] wording), decision 2 (the rotation
  sentence) and the FE-2 structure choice after Task 1's numbers.

- 2026-11-03, implementation (implementer, under the owner's "implement"
  instruction of 2026-11-03): Owner Decision 2 closed on the owner's
  implement instruction (2026-11-03) with the recommended option: accept
  the rotation-order sentence as written, including the recovery wording.
  FE-2 closed on the owner's implement instruction (2026-11-03) with the
  recommended option: the circular linked ring with a cursor (Task 1's
  measurements meet the plan's criterion for keeping it). O2 decided by
  Task 6's measurements: the payload proxy is outside the control band on
  the 1,024-sibling shape (1.043), so register row 9 is D there and the
  plan stops at Task 6 for the owner's ruling. Owner Decision 1 (the
  [ALT-WT-016] wording) awaits the owner; the theory edit is not
  committed. No new cross-family round was run by the implementer (Task 2
  rests on rounds 1 and 2 above; the completed-work review and the [WT-7]
  probe are owed).
- Cross-family review of the landed implementation (codex 0.161.0, 2026-11-03, range b01b9269..a9562dc6): blocker F1 (P2): the §7 all-tests gate (`pytest -m ""`, slow tests included) had been replaced by the fast suite in the Execution log. Disposition: reproduced; the gate was then run by the session lead in a detached worktree at a9562dc6 (`./.venv/bin/python -m pytest -m "" -q -p no:cacheprovider`): exit 0, no failures (output at the session scratchpad `series/impl/plan5/all-tests-gate.txt`). No production defect found. The reviewer confirmed: the replacement class is byte-identical to Task 4's block and lines 1–229 unchanged; `manager.py` unchanged; `_find` stops only on `_live_children` membership and compaction cannot strand a registered resident's pointer; insertion before `_turn` and the recovery rebuild in `_residents` order match the promoted [MA-1.8] and [MANAGER.18] sentences byte for byte; all five spec Replacement blocks present verbatim and the two theory blocks correctly held; the fourteen tests reproduce the RED/GUARD split (253 passed, 77 failed against the old class; 330 cases pass now) and every one of the 21 named mutations is caught by the named test; the Stop-if on register row 9 was correctly applied and the owner's ruling is on the row's disposition. Two corrections recorded here: (1) the Task 1 harness shuffles build order only for `select`; the payload and `frontiers` arms were built in fixed arm order, so the payload result is not described as build-order randomized, and the frontier-derivation increase (about 96 µs) accounts for most but not provably all of the payload increase (about 124 µs); (2) leaf assumption A1 is exactly "a newly launched task registers as a leaf"; the paraphrase "a child never precedes its parent in `_residents`" is false during handoff, where a later parent adoption is repaired by `recover` → `finish_recovery`, as the plan's rebuild-adjustment note already places it.
- owner acceptance applied 2026-11-03 ("I accept the theory edits"): the [ALT-WT-016] Replacement promoted into docs/program-theory.md (Owner Decision 1 closed as accepted); Status moved to promoted. Still open for completed: the register row 9 ruling and the [WT-7] possession probe.

## Execution log

Plan-authoring evidence (2026-11-03, author, at `b7eed9f9` plus the
uncommitted plan drafts; `python 3.14.9` from `./.venv`, after `. ./.envrc`,
from `/Users/van/Developer/weft`). Author scratch scripts live in
`/private/tmp/claude-501/-Users-van-Developer-weft/ac65a238-5c84-4ce8-8e8e-2287e7f6299f/scratchpad/series/plan5/redraft/ev/`
(called `EV/` below); they are not repository files. Each runs as
`./.venv/bin/python EV/<script> /Users/van/Developer/weft` unless noted
(`EV/candidate/cand_checks.py` also takes `EV/` as a second argument).
E12–E21 rest on these scratch scripts, which a zero-context implementer
cannot rerun; the reproducible path for every such claim is Task 1
(baseline), Task 3 (the tests themselves), Task 4's mutation runs, and
Task 6.
E1, E2, E4, E6 and E7 re-verify the replaced draft's evidence of the same
names; E8–E17 are new; E18–E21 are the fresh-eyes reviewer's; E22–E24 are the follow-up's; E25 and the reviewer's probe after it record cross-family round 1; E26 records the round-2 residual.

- E1 (wall time, baseline; `EV/e1_wall.py`, best of five
  `perf_counter_ns`; the replaced draft used a `sys.settrace` opcode
  counter, so this run reports time only):
  ```text
  case,S,L,best_ns
  retire_tip,256,1,1690084
  retire_tip,1024,1,26895750
  finish_recovery,1024,1,27315625
  finish_recovery,1024,4,116328917
  recover_nodefer,1024,4,119508584
  ```
- E2 (`sys.monitoring` INSTRUCTION count inside
  `weft/core/family_scheduling.py`, baseline; `EV/e2_counts.py`, two runs,
  identical, and identical to the replaced draft's E2):
  ```text
  S,retire_instr,finish_instr,recover_nodefer_instr
  64,37518,38652,39829
  256,567534,572124,576565
  1024,8954478,8972892,8990389
  ```
- E4 (spec complexity statements):
  `grep -n -i "O(1)\|O(log\|amortiz\|complexity\|linear time\|quadratic" docs/specifications/03-Manager_Architecture.md docs/specifications/07-System_Invariants.md docs/specifications/05-Message_Flow_and_State.md`
  → only plan-link titles ("Burst submission complexity", 03 line 49, 07
  line 1408, 05 line 2121), the retired "complexity-review-corrections"
  pointer (03 line 675), and the pending-index sentence "A bucket-local
  linear compaction is permitted with an amortized" (03 line 508). No
  frontier complexity statement and no rotation-order statement.
- E6 (baseline tests):
  `./.venv/bin/python -m pytest tests/core/test_family_scheduling.py tests/core/test_work_conserving_admission.py -q -rA | grep -cE "^PASSED"`
  → `35`; with `^FAILED` → `0`.
- E7 (resolution of cited surfaces):
  - `git rev-parse HEAD` → `b7eed9f9821a73b7a0ed923446f9309762a52646`;
    `git diff --stat b7eed9f9 -- weft/ tests/ docs/specifications docs/program-theory.md`
    → nothing; `wc -l weft/core/family_scheduling.py` → `431`.
  - Every backticked repository path in this plan (30 tokens) exists
    (`os.path.exists` over the extracted set → `missing: []`); every
    `file::test` target resolves by `grep "def <name>"`, and the two
    bare `::test_...` targets resolve to
    `tests/core/test_admission_recovery.py` and
    `tests/core/test_work_conserving_admission.py`.
  - Every `weft/core/manager.py` line cited in §3 and §4 was printed with
    `sed -n <n>p` and shows the named definition or call (1211, 1217, 1220,
    1501, 1503, 1504, 1645, 1653, 1672, 1701, 1774, 2252, 2301, 2365, 2388,
    2420, 2522, 2528, 2551, 2558, 2614, 2622, 2629, 2757, 2770, 5170, 5180,
    5186, 5660, 5662, 5766), as did `weft/core/tasks/base.py` lines 388,
    2085, 2436, 2456, 2486, 2531, 2632, `weft/core/runtime_identity.py`
    lines 237 and 250, and `weft/core/task_state.py` line 131.
    `grep -c "_publish_scheduler_checkpoint()" weft/core/manager.py` → `6`.
  - `weft/core/family_scheduling.py` line ranges in §3 were read from
    `cat -n` of the file at `b7eed9f9`.
  - Spec and theory anchors: 03 lines 34, 36, 445, 500, 502, 557, 565, 566,
    575; 07 lines 388, 1118, 1133, 1139, 1392, 1394; 05 lines 538, 916;
    `docs/program-theory.md` lines 274, 415, 425, 451, 453, 681, 777,
    790–794, 798, 941; reconciliation plan lines 5450 and 5547: each
    printed and matched.
- E8 (the two selection paragraphs):
  `diff <(sed -n 445p docs/specifications/03-Manager_Architecture.md) <(sed -n 1139p docs/specifications/07-System_Invariants.md)`
  → no output (byte-identical). The Replacement text was built
  mechanically: the current paragraph with the new sentence inserted after
  the single occurrence of "Active branches receive round-robin turns and
  offer newest eligible tip children first. " (a Python `str.replace` with
  a count assertion); the "Current text:" quotes were copied from the files
  by line number, not retyped.
- E9 (deferred recovery callers): `grep -rn "defer=True" weft tests`
  (excluding the definition) → `weft/core/manager.py:2627` (inside
  `_recover_scheduler_paths`, call starting at line 2622) and
  `tests/core/test_family_scheduling.py:425`.
- E10 (who reads `stacks`, who calls `frontiers`):
  `grep -rn "frontiers\|scheduler_frontiers\|\"stacks\"" weft/` outside the
  scheduler → `weft/core/task_state.py` lines 137–152 (reads `residents`
  and `reserved_queue`), `weft/core/manager.py` line 1216–1217 (the write),
  2528 (`_bootstrap_scheduler_evidence`: requires a Mapping, then
  `checkpoint.get("residents", {})` only, then `_adopt_scheduler_evidence`
  per resident and `_recover_scheduler_paths`, which rebuilds from
  residents), and 2558 (`_recover_unlaunched_reservations`: requires a
  Mapping, then `source_queue` and `reserved_queue` only). `grep -rn
  "stacks" --include=*.py weft tests bin integrations extensions` adds
  nothing else in `weft/`, `bin/`, `integrations/` or `extensions/`. So
  neither read-back site reads `stacks`, its contents, or its order.
  `scheduler.frontiers` has one caller in `weft/` (manager.py line 1217).
- E11 (assertions on `frontiers` or `stacks` in tests):
  `grep -rn "frontiers\b" tests/ | grep -v scheduler_frontiers | grep "==\|len(\|set("`
  → the 14 lines listed in §3 (`tests/core/test_family_scheduling.py` 72,
  80, 85, 123, 125, 143, 186, 223; `tests/core/test_manager_family_maintenance.py`
  730, 850, 889, 905; `tests/core/test_work_conserving_admission.py` 61;
  `tests/core/test_family_dispatch_scaling.py` 168), plus
  `tests/core/test_family_source_reconciliation.py` line 122 (`assert not
  ...frontiers`) and `tests/system/test_family_matrix.py` line 442
  (`stacks == [[parent.tid]]`). None asserts an order among two or more
  stacks.
- E12 (existing tests against the prototype): `EV/suite/test_ring_suite.py`
  is `tests/core/test_family_scheduling.py` with `FamilyScheduler` imported
  from the prototype (`EV/ring_proto.py`); `EV/candidate/test_candidate_suite.py`
  imports everything from the candidate module (E17). Both runs:
  `24 passed`.
- E13 (rotation equivalence; `EV/rotation_check.py`): eight recovered roots
  with seeded pending children, selections with launches only when the
  selected child's parent is a tip (in-place extension), baseline class
  against the ring: `rotation sequences equal on 500 of 500 seeds`. The
  first version also launched children that start new stacks and
  mismatched on 213 of 500 seeds, which is register row 3, by design.
- E14 (prototype timing; `EV/proto_bench.py`; a subclass, best of five
  rounds of medians; indicative only, Task 6 is the evidence). Columns:
  `retire` of the middle tip, `frontiers` (as the Manager consumes it,
  `[list(s) for s in frontiers]`), `finish_recovery`, all in ns; the
  payload proxy is the baseline's. With the filter derivation:
  ```text
  shape,arm,retire_ns,frontiers_ns,finish_recovery_ns
  1x64,FamilyScheduler,1417,417,69625
  1x64,Ring,500,1416,13917
  1x64,checkpoint_payload_floor_ns,249417
  1x1024,FamilyScheduler,14209,2750,13695542
  1x1024,Ring,1167,17042,231000
  1x1024,checkpoint_payload_floor_ns,30377416
  4x512,FamilyScheduler,31792,6541,16257333
  4x512,Ring,1000,36875,457917
  4x512,checkpoint_payload_floor_ns,34019709
  32x64,FamilyScheduler,94458,8000,4766583
  32x64,Ring,917,41375,461875
  32x64,checkpoint_payload_floor_ns,8071958
  1024x1,FamilyScheduler,28003708,126000,28419167
  1024x1,Ring,666,179834,269500
  1024x1,checkpoint_payload_floor_ns,2311250
  ```
  The `_up`-chain derivation, same harness, `frontiers_ns` for the ring
  arm: 5667, 79375, 175542, 176792, 311750 (1.7–4.8× the filter). The
  largest derivation increase relative to the payload proxy is 1024x1:
  (179834 − 126000) / 2311250 = 2.3 %.
- E15 (property test and mutants; `EV/prop_check.py`, `EV/mutants.py`):
  300 seeds × 400 steps with the generator of Task 3 T1 and the full oracle
  (childless ring, `_live_parent` against the ancestry scan, frontier set
  against the reference algorithm, `len(_up) <= 2 * len(_residents)`):
  `300 seeds x 400 steps agree; max final residents 106`. Mutants, first
  failing seed: delete instead of contract → 0; contract instead of delete
  → 0; counts not transferred (`-= 1`) → 1 or 2 depending on the frontier
  derivation in use; no compaction → 0 (only through the `_up` bound
  assertion). `EV/baseline_public_check.py`: the public part of the oracle
  (tips equal childless residents; frontier set equals the reference) holds
  for the baseline class and the ring on 200 seeds × 400 steps, so T1 is a
  GUARD.
- E16 (late-ancestor order; `EV/late_ancestor.py`): recover 100; launch
  102 with ancestry `("100", "101")`; then launch 101 with ancestry
  `("100",)`. Baseline frontiers `[("100", "101"), ("100", "102")]`;
  ring with the filter derivation `[("100", "101"), ("100", "101",
  "102")]`; ring with the `_up`-chain derivation `[("100", "101"), ("100",
  "102")]`; today's `finish_recovery` on the same residents would build
  `[("100", "101", "102")]`. After `retire("101")` all three give
  `[("100", "102")]`.
- E17 (candidate module; `EV/candidate/family_scheduling_candidate.py` is
  lines 1–229 of `weft/core/family_scheduling.py` unchanged plus the Task 4
  class): `ruff format --config pyproject.toml --check` → `1 file already
  formatted`; `ruff check --config pyproject.toml` → `All checks passed!`;
  with `--config "lint.mccabe.max-complexity=6"` → `C901 retire is too
  complex (7 > 6)`, so `retire` measures 7 against the repository limit of
  10; `mypy --config-file pyproject.toml` → `Success: no issues found in 1
  source file`. `EV/candidate/cand_checks.py` (E15's oracle and E13's
  rotation check against the candidate) → `300 seeds x 400 steps, full
  oracle ... holds` and `candidate rotation equal to baseline cursor on 500
  of 500 seeds`. `EV/candidate/red_checks.py` (Task 3 T2, T3, T4, T10
  scripts on both classes):
  ```text
  T2 baseline: ... after retire: (('100', '101', '102'), ('300',), ('200',))
  T2 ring    : ... after retire: (('300',), ('100', '101', '102'), ('200',))
  T3 baseline: ... after retire: (('200', '201', '202'), ('100', '102'))
  T3 ring    : ... after retire: (('100', '102'), ('200', '201', '202'))
  T4 baseline and ring: identical frontier sequences, ending (('100',), ('200',))
  T10 baseline: [103, 203, 303, 103]
  T10 ring    : [103, 203, 103, 303]
  ```
  (T10's first element is the initial selection; the asserted tail is
  `[203, 103, 303]`.) `EV/candidate/t5_check.py` (T5's script): `before 102
  101 15 12`, `find 100`, `after 100 100 100`. `EV/candidate/t6t8_check.py`
  (T6's sibling case and T8's script, both classes): sibling sequence `[111,
  211, 112, 212, 113, 213]` on both; T8 frontier sets equal on both, 4
  stacks, rebuilt ring order `(('100', '101', '103', '104'), ('201',),
  ('202',), ('203',))`.
Fresh-eyes evidence (2026-11-03, reviewer, same tree and toolchain;
scripts in `/private/tmp/claude-501/-Users-van-Developer-weft/ac65a238-5c84-4ce8-8e8e-2287e7f6299f/scratchpad/fe5/` (called `FE/`), each taking the candidate module path
`EV/candidate/family_scheduling_candidate.py` as its argument; the
baseline is the imported `weft.core.family_scheduling`):

- E18 (non-leaf launch, pending side; `FE/o1.py`): recover 100; `launched`
  102 with ancestry `("100", "101")`; `launched` 101 with ancestry
  `("100",)`; `add_pending` 103 with ancestry `("100", "101")` and 104 with
  ancestry `("100", "101", "102")`; `retire("102")`; `select()`. Baseline
  selects 104 (102's bucket merged into the tip 101); the ring selects 103
  (102's bucket merged into 100, which is not a tip). Ring counts after the
  exit: `{'100': 1, '101': 0}`.
- E19 (`select` cost; `FE/sel.py`, best of five, 200 non-consuming
  `select` calls per round, one root with S recovered leaf children and
  three pending children per eligible tip): ns per call
  ```text
  S=64   every tip eligible: baseline 324,   ring 794
  S=64   far end only:       baseline 3635,  ring 3797
  S=1024 every tip eligible: baseline 329,   ring 9622
  S=1024 far end only:       baseline 52807, ring 62003
  OrderedDict ring (tip pass only): 297, 5868, 331, 93735 (same four rows)
  ```
- E20 (T6 falsifiability; `FE/t6.py`): T6 as first drafted (no pending
  additions): the ring and the "rotate only the chosen entry" mutant both
  agree with the cursor model on 50 of 50 seeds. With pending additions
  during the first 40 selections: ring 50 of 50, mutant 8 of 50;
  `FE/t6_base.py` (the same model against the baseline class) 50 of 50, so
  T6 stays a GUARD.
- E21 (mid-life recovery order; inline script): `launched` 100, 200, then
  101 (child of 100) gives `(("100", "101"), ("200",))`; `recover("300",
  "300", ())` then gives `(("200",), ("100", "101"), ("300",))`.
- D3 cross-check (inline script): a subclass that deletes `_up[tid]` at
  every childless exit raises `KeyError: '100'` after launches 100, 101,
  102 (a chain), exits 101 (interior), 102, then 100, and a `launched` of a
  pending request whose ancestry ends at 101. That confirms D3's reason.

Follow-up evidence (2026-11-03, after the fresh-eyes pass, same tree and
toolchain; scripts in `EV/linked/`, i.e.
`/private/tmp/claude-501/-Users-van-Developer-weft/ac65a238-5c84-4ce8-8e8e-2287e7f6299f/scratchpad/series/plan5/redraft/ev/linked/`,
each run as `./.venv/bin/python EV/linked/<script> /Users/van/Developer/weft EV`).
Like E12–E21 these are scratch scripts; the reproducible path is Task 1,
Task 3, Task 4's mutation runs and Task 6.

- E22 (linked-ring candidate; `EV/linked/family_scheduling_linked.py` is
  lines 1–229 of `weft/core/family_scheduling.py` unchanged, `EV/linked/head.py`,
  plus the Task 4 class, `EV/linked/cls.py`, byte for byte): `ruff format
  --config pyproject.toml --check` → `1 file already formatted`; `ruff check
  --config pyproject.toml` → `All checks passed!`; C901 measured with
  `--config "lint.mccabe.max-complexity=1"`: `select` 7, `launched` 6,
  `retire` 5, `finish_recovery` 5, `_contract` 4; `mypy --config-file
  pyproject.toml` → `Success: no issues found in 1 source file`.
  `EV/linked/test_linked_suite.py` (today's
  `tests/core/test_family_scheduling.py` with only its imports pointed at
  the candidate; `diff` against the repository file shows nothing else) →
  `24 passed`. `EV/linked/linked_checks.py`:
  ```text
  linked: 300 seeds x 400 steps, full oracle holds (childless ring, links, live parent, frontier set, _up bound)
  linked rotation equal to baseline cursor on 500 of 500 seeds
  T2 linked  : ((('300', '301'), ('100', '101', '102'), ('200',)), (('300',), ('100', '101', '102'), ('200',)))
  T3 linked  : (('100', '102'), ('200', '201', '202'))
  T4 linked  : [(('100', '102'), ('100', '103'), ('200',)), (('100', '103'), ('200',)), (('100',), ('200',))]
  T10 linked  : [103, 203, 103, 303]
  T5 before 102 101 15 12 find 100 after 100 100
  T6 fixed baseline 50 of 50
  T6 fixed linked 50 of 50
  T6 fixed mutant turn=successor of start 0 of 50
  T6 fixed mutant never advance 0 of 50
  T6b baseline 0 of 50
  T6b linked 50 of 50
  T6b mutant insert at head 0 of 50
  T6b mutant deleted turn -> predecessor 10 of 50
  handoff baseline: ((('101',),), (('100', '101'),))
  handoff linked  : ((('101',),), (('100', '101'),))
   linked internals before: 1 ['101'] None
   linked internals after: 1 ['101'] 100 False
  handoff mutant (incremental adoption): ((('101',),), (('100', '101'), ('100',)))
  deferred retire baseline : ((('100',),), (('100',),), 160)
  deferred retire linked   : ((('100',),), (('100',),), 160)
  deferred retire old dict : raises KeyError('101')
  deferred retire mutant (no merge): ((('100',),), (('100',),), 150)
  deferred launch baseline: ((('100', '101'),), (('100',),))
  deferred launch linked  : ((('100', '101'),), (('100',),))
  deferred launch mutant (scan residents): raises KeyError('100')
  deferred launch mutant (finish_recovery does not rebuild): ((('100', '101'),), ())
  T8 True 4 (('100', '101', '103', '104'), ('201',), ('202',), ('203',))
  ```
  ("T6b" in the script is Task 3's T11; "handoff" is T12; "deferred retire"
  is T13; "deferred launch" is T14. The T2, T3, T4 and T10 baseline lines
  equal E17's.) The first draft's dict candidate also passes the deferred
  launch script (it steps over the deferred parent and `finish_recovery`
  repairs it); its regression in the deferred window was `retire`'s
  `KeyError`.
- E23 (`select` cost; `EV/linked/select_bench.py`: one root with S
  recovered leaf children; shapes head hit, far-end hit and full miss as in
  Task 1; 200 non-consuming calls per timing; five interleaved rounds, each
  shuffling (arm, shape, S); best round median of five timings, ns per
  call; arms: the imported baseline, the linked candidate, the OrderedDict
  variant, and the first draft's dict-snapshot candidate
  `EV/candidate/family_scheduling_candidate.py`):
  ```text
  shape,S,baseline,linked,ordereddict,dict-snapshot
  head,64,294,246,301,758
  head,256,311,254,305,2087
  head,1024,319,261,324,9201
  far,64,3564,2228,6007,3680
  far,256,13219,8026,23315,13655
  far,1024,51681,32211,95150,59586
  miss,64,8587,5665,13766,8645
  miss,256,34866,22285,54579,34268
  miss,1024,147279,99169,237852,155310
  ```
  No control arm was run, so these are indicative; Task 1 and Task 6 carry
  the band.
- E24 (`frontiers` as the Manager consumes it, `[list(s) for s in
  frontiers]`; `EV/linked/frontiers_bench.py`: C chains of depth L below
  root `100`, recovered in one deferred sweep; best of five medians of 50
  calls, ns):
  ```text
  shape,baseline,linked,dict-snapshot
  1x64,375,1500,1458
  1x1024,2250,19708,19459
  4x512,6625,42938,42250
  32x64,8875,48480,46604
  1024x1,61771,157167,132042
  ```
  The largest increase over today, 1024x1, is 95,396 ns, which is 4.1 % of
  E14's payload proxy for that shape (2,311,250 ns).
- E25 (cross-family round 1 F1 fix, 2026-11-03, same tree and toolchain;
  scripts in `EV/linked/`, run from `/Users/van/Developer/weft` after
  `. ./.envrc`). `EV/linked/cls.py` (the Task 4 block above, byte for byte:
  the block extracted from this plan and `diff`ed against the file prints
  nothing) now has `_find` test `_live_children` (registered) instead of
  `_residents`, with matching docstrings in the class, `_find`, `recover`
  and one `launched` comment; the round-1 file is kept as
  `EV/linked/cls.py.r1a`. `EV/linked/family_scheduling_linked.py` is
  `EV/linked/head.py` (lines 1–229 of `weft/core/family_scheduling.py`,
  `diff` prints nothing) followed by `EV/linked/cls.py` (`diff` against the
  concatenation prints nothing). On
  `EV/linked/family_scheduling_linked.py`: `./.venv/bin/ruff format
  --config pyproject.toml --check` → `1 file already formatted`;
  `./.venv/bin/ruff check --config pyproject.toml` → `All checks passed!`;
  C901 with `--config "lint.mccabe.max-complexity=1" --select C901`:
  `select` 7, `launched` 6, `retire` 5, `finish_recovery` 5, `_contract`
  4, `_find` 3 (unchanged from E22); `./.venv/bin/mypy --config-file
  pyproject.toml` → `Success: no issues found in 1 source file`.
  `EV/linked/test_linked_suite.py` (today's
  `tests/core/test_family_scheduling.py` with only its two import lines
  pointed at the candidate; `diff` shows nothing else), run with
  `./.venv/bin/python -m pytest test_linked_suite.py -q -p
  no:cacheprovider` from `EV/linked/` → `24 passed, 1 warning` (the
  warning is the unregistered `shared` mark outside the repository's
  pytest configuration); the repository file itself against the baseline
  → 24 `PASSED`. `./.venv/bin/python EV/linked/linked_checks.py
  /Users/van/Developer/weft EV` (exit 0; full output saved as
  `EV/linked/linked_checks.r1b.out`): every line of E22's output is
  reproduced unchanged (the 300-seed oracle, 500 of 500 rotation seeds, T2,
  T3, T4, T5, T6, T6b with its mutants, handoff, deferred retire and
  deferred launch with their mutants, T8), followed by the new section 10
  (the round-1 retained-pointer witness, plus the round-1 `_find` as the
  mutant `FindAnyResident`):
  ```text
  F1 launch 103 under deferred 101 baseline: ((('100', '102'), ('100', '101', '103')), 'n/a', (('100', '101', '102'), ('100', '101', '103')))
  F1 launch 103 under deferred 101 linked  : ((('100', '101', '102'), ('100', '101', '103')), '100', (('100', '101', '102'), ('100', '101', '103')))
  F1 launch 103 under deferred 101 mutant (_find stops at any resident): raises KeyError('101')
  F1 retire 102 under deferred 101 baseline: ((('100',),), (('100', '101'),))
  F1 retire 102 under deferred 101 linked  : ((('100',),), (('100', '101'),))
  F1 retire 102 under deferred 101 mutant (_find stops at any resident): raises KeyError('101')
  F1 retire deferred 101 baseline: ((('100', '102'),), (('100', '102'),), 160)
  F1 retire deferred 101 linked  : ((('100', '102'),), (('100', '102'),), 160)
  F1 retire deferred 101 mutant (_find stops at any resident): ((('100', '102'),), (('100', '102'),), 160)
  F1 compaction in the deferred window: up keys ['100'] registered ['100'] residents ['100', '101']
   then launch 103 under 101: live parent 100 frontiers (('100', '101', '103'),)
   after finish_recovery: (('100', '101', '103'),) live parent of 103 101
  ```
  Reading: in the launch case the tuple is (frontiers before
  `finish_recovery`, the child's live parent before it, frontiers after);
  the candidate attaches 103 to 100 and the rebuild gives the baseline's
  paths. The before-rebuild frontiers differ from the baseline's
  (`('100', '101', '102')` against `('100', '102')`) because `frontiers`
  filters each tip's ancestry by `_residents`, which includes the deferred
  101; both are hints inside a deferred window that the one caller closes
  immediately, and the rebuilt frontiers are equal. The retire-102 case
  contracts to `('100',)` exactly as the baseline does. Retiring the
  deferred 101 itself takes the deferred path and matches the baseline,
  including the merged bucket (160 selected); the mutant does not reach
  `_find` there, so that case is a guard, not a mutation witness. The
  compaction probe is the retire-102 case: three pointers for one
  registered resident trigger `_compact_up`, which keeps only the
  registered key `100` and drops the deferred 101's retained pointer; a
  later launch under 101 then steps over it to 100. Before the fix, the
  same witness against the round-1 `EV/linked/cls.py.r1a` raised
  `KeyError('101')` in both the launch and the retire-102 cases (the
  reviewer's report, reproduced by the author with the same sequences in
  a one-off inline script before editing).
- Cross-family round 1 reviewer's indicative probe (codex 0.161.0,
  2026-11-03; the reviewer's numbers, not reproduced by the author; no
  control arm stated beyond the reviewer's note that the head difference
  was inside its control variation), `select` at S = 1,024, baseline
  against the linked ring: head hit 308 ns against 316 ns; far-end hit
  51.0 µs against 31.7 µs; full miss 137.9 µs against 93.7 µs. A second
  data point beside E23; Task 1 and Task 6 remain the evidence.
- E26 (cross-family round 2 F1 residual, 2026-11-03, same tree and
  toolchain; scripts in `EV/linked/`, run from `/Users/van/Developer/weft`
  after `. ./.envrc`). `EV/linked/cls.py` now has `retire` merge the
  pending bucket into `_parent(...)` for every resident (today's code,
  unchanged) and contract through `_find` only for a registered resident;
  only `retire` changed (its docstring included). The round-1b files are
  kept as `EV/linked/cls.py.r1b` and `EV/linked/linked_checks.py.r1b`.
  The Task 4 block above, extracted from this plan into
  `EV/linked/extracted.r2b.py`, `diff`s against `EV/linked/cls.py` with no
  output; `EV/linked/head.py` still equals lines 1–229 of
  `weft/core/family_scheduling.py`, and
  `EV/linked/family_scheduling_linked.py` equals their concatenation (both
  `diff`s print nothing). On `EV/linked/family_scheduling_linked.py`:
  `./.venv/bin/ruff format --config pyproject.toml --check` → `1 file
  already formatted`; `./.venv/bin/ruff check --config pyproject.toml` →
  `All checks passed!`; C901 with `--config
  "lint.mccabe.max-complexity=1" --select C901`: `select` 7, `launched` 6,
  `retire` 5, `finish_recovery` 5, `_contract` 4, `_find` 3 (unchanged
  from E25; limit 10); `./.venv/bin/mypy --config-file pyproject.toml` →
  `Success: no issues found in 1 source file`.
  `EV/linked/test_linked_suite.py` (still today's
  `tests/core/test_family_scheduling.py` with only its two import lines
  changed) from `EV/linked/` with `./.venv/bin/python -m pytest
  test_linked_suite.py -q -p no:cacheprovider` → `24 passed, 1 warning`;
  `./.venv/bin/python -m pytest tests/core/test_family_scheduling.py -q`
  against the baseline → 24 passed. `./.venv/bin/python
  EV/linked/linked_checks.py /Users/van/Developer/weft EV` (exit 0; full
  output saved as `EV/linked/linked_checks.r2b.out`): `diff` against
  `EV/linked/linked_checks.r1b.out` shows only the four lines of the new
  section 11 (the reviewer's sequence, with the round-1b `retire` as the
  mutant `MergeIntoFind`; the section asserts the three selections and
  the shared rebuilt frontier), so every earlier line, sections 1–10
  included, is reproduced unchanged:
  ```text
  F1 residual retire 102 under deferred 101 baseline: ((('100',),), (('100', '101'),), 160)
  F1 residual retire 102 under deferred 101 linked  : ((('100',),), (('100', '101'),), 160)
  F1 residual retire 102 under deferred 101 previous candidate (merge into _find): ((('100',),), (('100', '101'),), 150)
  F1 residual: baseline and candidate select 160 after recovery; the previous candidate selects 150
  ```
  Reading: each tuple is (frontiers before `finish_recovery`, frontiers
  after, the request `select` returns after it). All three agree on the
  frontiers; only the merge target differs. The baseline and the
  candidate merge 102's bucket into the deferred 101, so 101's bucket
  holds 150 and 160 and the newest is chosen; the round-1b candidate
  merged it into 100, which the rebuilt frontier `(100, 101)` asks only
  after 101. The prototype timings of E14 and E22–E23 were not re-run:
  the merge-target scan they omit stops at the first resident in the
  reversed ancestry, one step whenever the exiting resident's parent is
  a resident (as for the exiting tips of E14's shapes, by their
  construction; not re-measured), and Task 6 measures the real method.
- A1 citations (2026-11-03, live tree; `git diff --stat b7eed9f9 -- weft/
  tests/ docs/specifications docs/program-theory.md` prints nothing):
  printed with `sed -n` and matched: `weft/core/runtime_identity.py` lines
  35 (`_validate_ancestry`), 237 (`submission_lineage`), 250
  (`validate_submission_lineage`), 282 (its call in
  `runtime_identity_for_task`); `weft/core/spawn_requests.py` line 388
  (`submitter = submission_lineage(identity)` inside
  `_submit_spawn_request_with_payload`, which starts at line 328);
  `weft/core/manager.py` lines 1293, 1508, 1653, 1672, 1701, 2301, 2349,
  2388, 2622, 2629, 5170, 5180–5186, 5191, 5813;
  `weft/core/family_scheduling.py` line 162 (`if self.physical_count > 2 *
  self.count:`).

- Not verified: no edit was made to `weft/core/family_scheduling.py` and
  nothing ran under the repository's pytest configuration against the new
  code (the candidates ran from the scratch directory); no module-level
  sweep, control band, payload proxy band, drain maximum, or PostgreSQL run
  exists yet (Tasks 1 and 6). Assumption A1's three reasons are established
  by reading only.

### Implementer (2026-11-03)

Task 0 (read, answer, record). `BASE=b01b92690e1b61065d31c0a12c8b20e924310c66`
(`git rev-parse HEAD`; it descends from `b7eed9f9`: `git merge-base
--is-ancestor b7eed9f9 HEAD` succeeds). `git diff --stat b7eed9f9 --
weft/core/family_scheduling.py weft/core/manager.py` prints nothing, nor does
the same over `docs/specifications/03-Manager_Architecture.md`,
`docs/specifications/07-System_Invariants.md`, `docs/program-theory.md` and
`tests/core/test_family_scheduling.py`, so every §3 line number still holds
(re-checked: 03 lines 36, 445, 566; 07 lines 1139, 1394; the two selection
paragraphs are still byte-identical). Verify:
`./.venv/bin/python -m pytest tests/core/test_family_scheduling.py
tests/core/test_work_conserving_admission.py -q -rA -p no:cacheprovider`
→ 35 `PASSED` lines, 0 `FAILED` lines (python 3.14.9).

Comprehension answers:

1. The ring holds exactly the childless residents because a baseline stack is
   "a childless resident and its live ancestors": `launched` extends the
   stack whose tip is the new task's nearest live ancestor or starts a new
   one, and `retire`/`finish_recovery` keep only maximal paths, and a path is
   maximal exactly when its tip has no live descendant. The path is a
   function of its tip (the tip's ancestry filtered to residents), so one
   ring entry per childless resident loses nothing; `frontiers` derives the
   path on demand.
2. An exit with `k >= 1` live children touches no stack: the set of
   childless residents does not change (the exiting resident was not
   childless; its children keep their own children; its parent `p` loses one
   child and gains `k`, so it stays non-childless). Only `_live_children[p]
   += k - 1` changes, and the children reach `p` through `_up[tid]` on their
   next lookup.
3. Dead `_up` entries survive until compaction because a live descendant's
   pointer may still name the dead node (compression happens on lookup, not
   at exit), and a pointer chain must never end at a missing key. A dead
   entry can be dropped only when nothing points at it; `_compact_up`
   establishes that wholesale by rebuilding the table from registered
   residents alone, each pointing at its live parent.
4. Each selection asks branches in ring order starting at the cursor (the
   branch after the one that supplied the previous selection); a full miss
   leaves the cursor where it was. While no stack is created or deleted this
   is exactly the baseline cursor; a new stack is linked just before the
   cursor, so it is asked after every existing one; an extension or
   contraction keeps its place; a deleted stack is unlinked, and a deleted
   cursor passes to its successor. FIFO among independent families and the
   sibling fallback are unchanged.
5. `frontiers` costs one C-level `filter` over each tip's `ancestry_tids`
   plus a walk of the ring (S steps), so total tip ancestry length per call.
   The same Manager payload deep-copies every resident's `ancestry_tids`
   (tips included), so the derivation is bounded by the write it feeds;
   storing paths to make it cheaper would bring back the per-exit path
   maintenance this plan removes.
6. A launched child's scheduling parent is `_nearest_live(ancestry)`: the
   first TID from the end of the request's ancestry that has an `_up` entry,
   resolved by `_find`, which stops only at a registered resident and follows
   the pointer of a former or deferred one. A TID without an entry was never
   resident here, was compacted away, or is a fresh deferred adoption, so it
   is not registered; the scan is bounded by the ancestry length, which is
   what the baseline `launched` already filters in full.

Task 1 (baseline measurement, no code change; `weft/core/family_scheduling.py`
at `BASE`, identical to `b7eed9f9`). Scripts, all in the session scratchpad
`/private/tmp/claude-501/-Users-van-Developer-weft/ac65a238-5c84-4ce8-8e8e-2287e7f6299f/scratchpad/series/impl/plan5/`
(called `IMPL/` below): `IMPL/bench.py` (the harness), `IMPL/armload.py`
(compiles an arm from source into its own module object registered in
`sys.modules`), `IMPL/analyze.py` (summaries), `IMPL/arms/linked.py` (lines
1–229 of the baseline file followed by the Task 4 class extracted from this
plan; `diff` against the author's `EV/linked/cls.py` prints nothing; `ruff
format --check` and `ruff check` pass on it), `IMPL/arms/ordereddict.py`
(built from the same class by `IMPL/arms/make_od.py` as §4 "Alternatives
considered" describes: one `OrderedDict[int, str]`, `select` steps `stack_id,
tip = next(iter(ring.items()))` then `ring.move_to_end(stack_id)` before
asking), `IMPL/arms/baseline_copy.py` (an unmodified copy of the module, the
control arm). Run from the repository root after `. ./.envrc`:
`./.venv/bin/python IMPL/bench.py IMPL/task1 --arm baseline=import --arm
control=IMPL/arms/baseline_copy.py --select-arm linked=IMPL/arms/linked.py
--select-arm ordereddict=IMPL/arms/ordereddict.py` (all groups, five rounds),
then the `select` group again with `--only select --rounds 9
--select-timings 9` (`IMPL/t1sel_d.csv`; see the harness fix below). Raw
rows: `IMPL/task1.csv`, `IMPL/t1sel_d.csv`; summaries
`IMPL/task1.summary.csv`, `IMPL/t1sel_d.summary.csv`, `IMPL/task1.compact.csv`.

Method as the task states, with these concrete choices: shapes are one root
`100` with C disjoint chains of depth L (C·L + 1 residents; "S siblings" is
C = S, L = 1), built through the public API (`recover(..., defer=True)` per
resident, then `finish_recovery()`); `retire` is the middle chain's tip
(restored untimed by `launched` of the same request); `recover` adopts a new
leaf below that tip (restored by `retire`); `launched` is a new leaf under
that tip, and "under a dead parent chain of depth 64" is the same leaf with
64 never-resident TIDs appended to its ancestry (the trailing-scan case;
restored by `retire`); `frontiers` is timed as the Manager consumes it,
`[list(s) for s in frontiers]`; the payload proxy is the plan's `json.dumps`
of `stacks` plus `copy.deepcopy(evidence)` with the eight checkpoint keys.
`launched`, `frontiers` and payload rows use E14's shapes (1x64, 1x1024,
4x512, 32x64, 1024x1); `select` chain-count rows use depth 64. One per-call
timing is the median of N individually timed calls (N fixed per shape from
one probe of the reference arm, 3 to 101); a `select` timing is 200
non-consuming calls in ns per call. Shapes run in a shuffled order each
round and every (arm, call) pair within a shape is shuffled; gc is disabled
inside each timing. Instruction counts use `sys.monitoring` exactly as the
task states. Timer: `perf_counter_ns` (resolution 41.7 ns on this machine).

Harness fix (Stop-if for the band): the first full run put the control at
0.880 (`select` S = 1,024 full miss) and 0.922 (far-end hit) of baseline in
every round, because the harness built each arm's scheduler in fixed arm
order before timing; with the build order shuffled as well, two re-runs
gave 0.980 and 0.969, and head-hit rows (about 290 ns per call) still
wandered to 1.07, so the `select` group was re-run with nine rounds and
nine timings per round. That run's band is 0.982–1.048. Two reported, not
gated, rows remain outside 0.95–1.05 in per-shape medians and are
attributed to timer granularity, not the harness: `frontiers` on 1x64
(0.899: 375 ns against 417 ns, one 41.7 ns timer tick) and the drain
maximum on the 1,024 chain (0.804: a single-call maximum of about 20 µs,
the largest outlier of 1,025 calls). Every other row, gated or not, has a
control/baseline per-shape median in 0.966–1.049.

Instruction counts at the baseline (`sys.monitoring`; S siblings at L = 1
and S chains at L = 4; the `retire` and `finish_recovery` columns at L = 1
equal E2 exactly; `recover` adopts a leaf below the middle tip, so it is
not E2's shape and differs from E2's column by about 1 %):

```text
64x1,retire_tip,37518
64x1,finish_recovery,38652
64x1,recover_leaf,38755
64x4,retire_tip,40121
64x4,finish_recovery,158940
64x4,recover_leaf,159073
256x1,retire_tip,567534
256x1,finish_recovery,572124
256x1,recover_leaf,572227
256x4,retire_tip,578009
256x4,finish_recovery,2306652
256x4,recover_leaf,2306785
1024x1,retire_tip,8954478
1024x1,finish_recovery,8972892
1024x1,recover_leaf,8972995
1024x4,retire_tip,8996441
1024x4,finish_recovery,35965020
1024x4,recover_leaf,35965153
```

Baseline wall times (ns, range of round medians and median over rounds) with
the control ratio and, for `select`, the two prototype arms' ratios
(`linked` is the Task 4 class, `ordereddict` the alternative):

```text
group,shape,op,baseline_round_median_range_ns,baseline_median_ns,control/baseline,linked/baseline,ordereddict/baseline
e1,256x4,recover_leaf,7207979-7314375,7248271,1.006,,
e1,256x4,retire_tip,1821750-1845875,1829666,0.998,,
e1,256x4,finish_recovery,7175292-7317958,7290875,0.999,,
e1,256x1,recover_leaf,1732062-1753354,1743812,1.002,,
e1,256x1,finish_recovery,1727500-1748479,1733271,1.002,,
e1,256x1,retire_tip,1707166-1718062,1711000,0.995,,
e1,1024x4,retire_tip,30392020-30907812,30773250,0.998,,
e1,1024x4,finish_recovery,121312250-122925375,122164375,0.995,,
e1,1024x4,recover_leaf,120576208-122703167,121791208,0.998,,
e1,1024x1,finish_recovery,28211167-28461750,28400875,0.998,,
e1,1024x1,retire_tip,27974292-28323875,28105625,0.997,,
e1,1024x1,recover_leaf,28147458-28648125,28499916,0.998,,
e1,64x4,recover_leaf,508709-513500,511458,1.000,,
e1,64x4,finish_recovery,507375-514958,510250,1.004,,
e1,64x4,retire_tip,119416-122083,121708,0.984,,
e1,64x1,recover_leaf,120542-123792,121083,0.995,,
e1,64x1,retire_tip,111834-112917,112750,0.998,,
e1,64x1,finish_recovery,119625-122792,120041,1.006,,
sweep,12x512,retire_tip,109125-110334,109500,0.998,,
sweep,12x512,finish_recovery,55839750-57653416,56272083,1.007,,
sweep,2x128,finish_recovery,497875-513917,513000,0.992,,
sweep,2x128,retire_tip,4000-4167,4000,1.000,,
sweep,1x128,retire_tip,2000-2250,2167,0.981,,
sweep,1x128,finish_recovery,240125-247834,241500,1.012,,
sweep,9x1024,retire_tip,141292-147875,142250,1.008,,
sweep,9x1024,finish_recovery,165974041-167738042,166707000,1.000,,
sweep,10x128,finish_recovery,3120313-3218437,3145250,1.001,,
sweep,10x128,retire_tip,25250-26250,25541,0.994,,
sweep,2x512,finish_recovery,7175978-7547229,7203604,1.000,,
sweep,2x512,retire_tip,12334-12959,12458,1.000,,
sweep,1x1024,finish_recovery,13639646-14029833,13683187,1.003,,
sweep,1x1024,retire_tip,11125-11417,11292,0.989,,
sweep,4x256,retire_tip,14500-14750,14625,0.991,,
sweep,4x256,finish_recovery,3978438-4107604,4015104,0.998,,
sweep,16x1024,finish_recovery,339439750-345757542,340922167,0.997,,
sweep,16x1024,retire_tip,327375-332458,329500,0.995,,
sweep,16x128,finish_recovery,5838750-5974917,5923583,0.989,,
sweep,16x128,retire_tip,49542-51500,49708,1.005,,
sweep,16x64,retire_tip,27417-27959,27542,1.000,,
sweep,16x64,finish_recovery,1709458-1769208,1724292,0.997,,
sweep,8x256,retire_tip,33541-33834,33667,1.004,,
sweep,8x256,finish_recovery,8829875-8998375,8891500,0.998,,
sweep,2x64,finish_recovery,141708-148209,145125,0.994,,
sweep,2x64,retire_tip,2125-2458,2333,1.000,,
sweep,32x64,retire_tip,87042-89375,87625,0.996,,
sweep,32x64,finish_recovery,4789667-4888500,4819167,0.999,,
sweep,8x64,finish_recovery,697375-701584,698625,1.002,,
sweep,8x64,retire_tip,10333-10666,10500,0.992,,
sweep,16x512,retire_tip,167208-171458,170167,0.988,,
sweep,16x512,finish_recovery,83419208-86168792,83638708,1.001,,
sweep,32x512,retire_tip,509500-546083,511833,0.997,,
sweep,32x512,finish_recovery,220348250-224015584,220658375,1.000,,
sweep,9x128,finish_recovery,2769666-2803666,2787687,0.997,,
sweep,9x128,retire_tip,21583-22375,21875,1.000,,
sweep,9x512,retire_tip,73125-75333,73167,1.004,,
sweep,9x512,finish_recovery,40442083-41954584,40587417,1.002,,
sweep,1x64,finish_recovery,67750-70917,70625,0.966,,
sweep,1x64,retire_tip,1333-1334,1333,0.969,,
sweep,8x128,retire_tip,18459-18792,18666,1.002,,
sweep,8x128,finish_recovery,2402271-2480666,2412104,0.998,,
sweep,1x512,finish_recovery,3431292-3491875,3460209,1.003,,
sweep,1x512,retire_tip,5958-6084,6000,1.000,,
sweep,4x1024,retire_tip,51500-53125,51791,1.008,,
sweep,4x1024,finish_recovery,63546583-64864708,63891875,1.002,,
sweep,4x512,retire_tip,26500-27042,26708,1.009,,
sweep,4x512,finish_recovery,15381667-15660958,15532625,0.994,,
sweep,12x1024,retire_tip,212459-221084,215375,1.000,,
sweep,12x1024,finish_recovery,228592750-229629500,228986708,1.006,,
sweep,32x128,retire_tip,155834-160625,156625,1.009,,
sweep,32x128,finish_recovery,16387729-16668145,16574563,1.002,,
sweep,32x256,finish_recovery,59313417-60408417,59916792,1.002,,
sweep,32x256,retire_tip,278625-282042,279291,1.000,,
sweep,1x256,finish_recovery,878417-915250,906167,0.993,,
sweep,1x256,retire_tip,3208-3708,3500,1.000,,
sweep,4x64,retire_tip,4542-4583,4583,1.000,,
sweep,4x64,finish_recovery,307417-317167,312334,0.994,,
sweep,8x512,finish_recovery,34366729-34898125,34729521,1.000,,
sweep,8x512,retire_tip,61708-62208,61958,1.004,,
sweep,10x1024,finish_recovery,191313750-194520375,191738542,0.999,,
sweep,10x1024,retire_tip,163750-168416,164417,0.997,,
sweep,16x256,retire_tip,90792-94625,92042,0.996,,
sweep,16x256,finish_recovery,21982646-22714416,22053854,1.001,,
sweep,2x1024,finish_recovery,28963624-29585000,29543333,0.998,,
sweep,2x1024,retire_tip,24291-25084,24417,1.003,,
sweep,8x1024,retire_tip,119875-121167,120333,1.007,,
sweep,8x1024,finish_recovery,141505125-143832542,142783333,0.997,,
sweep,2x256,retire_tip,6834-6958,6917,1.000,,
sweep,2x256,finish_recovery,1878333-1936625,1886834,0.999,,
sweep,4x128,finish_recovery,1077167-1094959,1082458,1.009,,
sweep,4x128,retire_tip,8084-8458,8208,0.995,,
sweep,12x128,retire_tip,31958-32875,32625,1.001,,
sweep,12x128,finish_recovery,3895187-4048792,3943145,1.006,,
sweep,32x1024,finish_recovery,888097333-893812458,892760458,0.998,,
sweep,32x1024,retire_tip,1006167-1042334,1011459,0.996,,
sweep,10x512,finish_recovery,46282250-47434625,47038125,0.997,,
sweep,10x512,retire_tip,84125-88000,84917,0.995,,
mixed,1x1024+512,retire_tip,6831750-6991375,6919000,0.998,,
mixed,1x1024+512,finish_recovery,19775417-20346125,19965208,0.993,,
mixed,1x1024+1000,finish_recovery,39341458-39891625,39534541,0.989,,
mixed,1x1024+1000,retire_tip,26301542-26999542,26504542,0.999,,
mixed,1x1024+64,finish_recovery,13776208-14002375,13799000,0.997,,
mixed,1x1024+64,retire_tip,127167-129208,128583,1.005,,
launch,32x64,launched_tip,2000-2083,2000,1.020,,
launch,32x64,launched_dead64,2709-2833,2750,1.000,,
launch,1x1024,launched_dead64,16583-16875,16709,1.002,,
launch,1x1024,launched_tip,15875-16125,15917,1.016,,
launch,1x64,launched_tip,1209-1291,1291,1.000,,
launch,1x64,launched_dead64,1875-1917,1916,0.979,,
launch,4x512,launched_dead64,10583-10792,10625,1.000,,
launch,4x512,launched_tip,9875-10125,9959,1.004,,
launch,1024x1,launched_dead64,16375-17583,17000,1.049,,
launch,1024x1,launched_tip,14250-15708,15167,1.022,,
frontiers,1x64,frontiers,334-417,417,0.899,,
frontiers,32x64,frontiers,8208-9125,8833,1.014,,
frontiers,4x512,frontiers,6292-6542,6459,0.987,,
frontiers,1024x1,frontiers,54459-55708,55084,1.004,,
frontiers,1x1024,frontiers,1709-2083,2042,1.020,,
payload,4x512,payload,34441125-35851208,34682583,1.008,,
payload,1x64,payload,289834-292333,291292,1.003,,
payload,1x1024,payload,31392396-31969063,31476666,1.007,,
payload,1024x1,payload,2850645-2906958,2874104,1.008,,
payload,32x64,payload,9095291-9414250,9278041,0.999,,
drain,chain1024,median,5875-6291,6167,0.973,,
drain,chain1024,max,21583-34292,24250,0.804,,
drain,chain1024,total,6137821-6463014,6257806,0.992,,
drain,siblings1024,median,6841416-6932291,6899125,1.009,,
drain,siblings1024,max,28333542-28706625,28458292,1.002,,
drain,siblings1024,total,9582102257-9643803379,9629189498,0.998,,
select,4x64,miss,726-747,732,0.999,0.765,1.516
select,2x64,miss,442-501,456,1.002,0.836,1.454
select,8x64,miss,1256-1365,1269,0.994,0.736,1.553
select,16x64,head,282-300,290,0.990,0.834,1.024
select,S1024,far,51245-52462,51741,1.007,0.652,1.918
select,1x64,head,265-291,277,0.986,0.863,1.040
select,S256,head,267-343,305,1.016,0.830,1.030
select,32x64,miss,4306-4589,4453,0.993,0.682,1.611
select,S64,far,3418-3614,3550,0.984,0.639,1.718
select,2x64,head,278-287,283,0.996,0.841,1.039
select,1x64,miss,290-323,320,0.994,0.925,1.303
select,4x64,head,278-325,284,1.000,0.838,1.035
select,8x64,head,267-288,281,1.007,0.840,1.043
select,S1024,miss,138420-159804,142419,0.982,0.674,1.673
select,S64,head,286-304,291,0.986,0.832,1.031
select,32x64,head,267-302,290,1.000,0.834,1.038
select,S1024,head,265-310,293,1.048,0.904,1.078
select,16x64,miss,2322-2416,2355,0.994,0.697,1.580
select,S256,miss,35245-36647,35917,1.001,0.634,1.567
select,S256,far,13498-14228,13791,0.996,0.601,1.741
select,S64,miss,8765-9039,8923,0.999,0.665,1.596
```

`_active_roots` decision (Task 4 "Not allowed"): no root-count map. The
full-miss `select` row is the only one that calls `_oldest_independent`
with every tip active, and it is at or under baseline for the linked arm
in this run (see `linked/baseline` above), so `_active_roots` does not
dominate a measured shape.

FE-2 structure choice: the plan's criterion for keeping the linked ring is
"at or under baseline on a head hit and on a full miss" (register row 15;
Task 6 gate). In Task 1 the linked prototype's per-shape medians against
the baseline are 0.60–0.93 on every `select` row (head hits 0.83–0.90,
full misses 0.63–0.93, far-end hits 0.60–0.65), and the OrderedDict arm is
1.02–1.08 on head hits but 1.30–1.92 on far-end hits and full misses, so
the linked ring stays the structure (closed on the owner's implement
instruction, below).

Task 2 (independent review before code): done differently. No new Tier-1
round was run by the implementer: the series brief for this implementation
forbids further delegation, and the plan's Review Log already records two
cross-family rounds (codex 0.161.0, 2026-11-03) over this exact design and
the Task 4 code block, with every finding dispositioned and the loop
closed. The open owner items Task 2 waits on are closed on the owner's
implement instruction (2026-11-03; §10 and the Review Log). Assumption A1's
three reasons were re-read at `BASE`: `weft/core/manager.py` is unchanged
since `b7eed9f9`, so the cited launch-commit, inbox-gate and adoption lines
are the ones the plan names. The completed-work cross-family review (Task 6)
is owed to the session lead.

Task 3 (RED tests; `tests/core/test_family_scheduling.py`, appended after
the existing 24 items, with `random`, `Callable`, `Collection`, `Mapping`,
`dataclass`, `field` and `SchedulingClass` imports added). Shared helpers:
`_reference_maximal_paths` (the `b7eed9f9` `finish_recovery` body over a
TID-to-ancestry mapping), `_nearest_resident`, `_childless`, the generator
`_FamilyModel` / `_drive_random_family` (one method per operation, chosen by
weight, so each stays under the C901 limit), `_expected_tip_candidate`
(today's cursor arithmetic), and `_CursorModel` for T11. T6's sibling-pass
case is the helper `_assert_sibling_pass_matches_cursor_arithmetic`, called
at the end of every T6 seed, so the test list stays fourteen tests. Against
the baseline module (`./.venv/bin/python -m pytest
tests/core/test_family_scheduling.py -q -p no:cacheprovider -rA -n0`; 354
items, 77 failed, 277 passed):

```text
GUARD passed: T1 test_frontier_tips_are_exactly_the_childless_residents_over_random_families 200/200
GUARD passed: T6 test_select_rotation_matches_cursor_arithmetic_while_stacks_are_fixed 50/50
GUARD passed: T8 test_recovery_rebuilds_the_same_frontiers_from_residents
GUARD passed: T13 test_retire_of_a_deferred_resident_merges_its_bucket_and_leaves_the_ring
GUARD passed: T14 test_launch_under_a_deferred_parent_is_repaired_by_finish_recovery
RED T2:  AssertionError: assert (('100', '101...',), ('200',)) == (('300',), ('...2'), ('200',))
RED T3:  AssertionError: assert (('200', '201...'100', '102')) == (('100', '102...'201', '202'))
RED T4:  AttributeError: 'FamilyScheduler' object has no attribute '_find'
RED T5:  AttributeError: 'FamilyScheduler' object has no attribute '_up'
RED T9:  AttributeError: 'FamilyScheduler' object has no attribute '_up'   (20/20 seeds)
RED T10: assert [203, 303, 103] == [203, 103, 303]
RED T11: 50/50 seeds fail (49 on the rotation assertion, e.g. seed 0
         assert ['1001', '100...1008', '1011'] == ['1003', '100...1011', '1001'];
         one on the selection, assert 1007 == 1005)
RED T12: AttributeError: 'FamilyScheduler' object has no attribute '_ring_tip'
RED T15: AttributeError: 'FamilyScheduler' object has no attribute '_live_parent'
existing 24 items: passed
```

The RED/GUARD split matches the Task 3 list exactly. The generator's T1
run at the baseline takes about 6 s for the whole file with `-n0`.

Task 4 (GREEN): `class FamilyScheduler` (line 230 to the end) replaced with
the Task 4 block; lines 1–229 unchanged (`weft/core/family_scheduling.py`
equals `IMPL/arms/linked.py`, `diff` prints nothing). `./.venv/bin/python
-m pytest tests/core/test_family_scheduling.py
tests/core/test_work_conserving_admission.py -p no:cacheprovider -rA` → 365
`PASSED`, 0 `FAILED`. `ruff format --check` and `ruff check` on both files
pass; `mypy` on both files: `Success: no issues found in 2 source files`.
Mutation runs (`IMPL/mutants.py` and `IMPL/mutants2.py`: edit the module,
run the named tests with `-n0`, restore; the module was restored and
re-`diff`ed against `IMPL/arms/linked.py` afterwards; output
`IMPL/mutants.out`), each with its first failing test:

```text
delete instead of contract             -> T1 seed 0   AssertionError: assert set() == {'1001'}
contract instead of delete             -> T1 seed 0   (tip set differs)
counts -= 1 instead of += k - 1        -> T1 seed 3   (tip set differs); T4 selected too
contracted stack deleted, re-appended  -> T2          (('100','101','102'),('200',),('300',)) order
no path compression                    -> T5          assert '102' == '101'
turn = successor of the starting stack -> T6 seed 0
tip pass never advances turn           -> T6 seed 0
sibling pass never advances turn       -> T6 seed 0   assert 112 == 211
ring built from all residents          -> T8
compaction disabled                    -> T9 seed 0   assert 1 <= (2 * 0)
_find returns the first dead node      -> T9 seed 11  KeyError: '1003'
_drop_stack forgets the predecessor    -> T9 seed 0   KeyError: 3
new stack linked at the head           -> T10         assert 203 == 103 (T11 selected too)
deleted turn passes to its predecessor -> T11 seed 0
adoption inserts incrementally         -> T12         assert (('100', '101'), ('100',)) == (('100', '101'),)
retire without the deferred branch     -> T13         KeyError: '101'
deferred retire skips the pending merge-> T13         assert 150 == 160
_find stops at any resident            -> T13         KeyError: '101'; T15 alone: KeyError: '101'
merge into the _find ancestor          -> T13         assert 150 == 160 (third case)
_nearest_live tests residents          -> T14         KeyError: '100'
finish_recovery does not rebuild       -> T14         assert () == (('100',),)
```

(The first version of the "merge into the `_find` ancestor" mutant called
`_find` before popping the count and raised `KeyError: '101'` instead; it
was rewritten to the round-1b shape, pop then `_find`, in `IMPL/mutants2.py`,
which selects 150 as E26 predicts.) No existing test needed an edit.

Task 5 (spec, CHANGELOG, mapping, backlinks): every Replacement block for
`docs/specifications/03-Manager_Architecture.md` and
`docs/specifications/07-System_Invariants.md` applied verbatim (extracted
from this plan by a script and substituted with count-one assertions; the
two selection paragraphs were byte-identical before the edit). The
CHANGELOG `[Unreleased]` / `### Changed` bullet is the plan's text,
re-wrapped to the file's 78-column bullets. The two `docs/program-theory.md`
blocks (Owner Decision 1) were applied, `./bin/check-doc-paths` passed with
them applied, the diff was saved as
`/private/tmp/claude-501/-Users-van-Developer-weft/ac65a238-5c84-4ce8-8e8e-2287e7f6299f/scratchpad/series/impl/plan5/theory.patch`,
and the edit was reverted with `git apply -R`; **the theory commit awaits the
owner's acceptance of Owner Decision 1**. `grep -c "Frontier Ring and
Contracted Ancestry"` prints 1 for each spec file.

Task 6 (measure after, gates, traceability; implementer, 2026-11-03).
Arms: `ring` is the edited module as imported; `baseline` and `control`
are both compiled from `git show b7eed9f9:weft/core/family_scheduling.py`
(`IMPL/arms/b7_baseline.py`, `IMPL/arms/b7_control.py`); the `select` rows
add the OrderedDict arm (`IMPL/arms/ordereddict.py`). Commands:
`./.venv/bin/python IMPL/bench.py IMPL/task6 --only
counts,e1,sweep,mixed,launch,frontiers,payload,drain --arm
baseline=IMPL/arms/b7_baseline.py --arm control=IMPL/arms/b7_control.py
--arm ring=import` (five rounds), then `--only select --rounds 9
--select-timings 9` with `--select-arm ordereddict=...` (`IMPL/task6sel`),
then `--only payload,frontiers --rounds 9` (`IMPL/task6pay`, a confirmation
run for the payload rows; its numbers replace the five-round payload and
`frontiers` rows below). Summaries: `IMPL/task6.summary.csv`,
`IMPL/task6sel.summary.csv`, `IMPL/task6pay.summary.csv`,
`IMPL/task6.compact.csv`. Control/baseline per-shape medians: 0.965–1.023
on every row except `frontiers` 1x64 (1.109, one timer tick at 375–417 ns;
reported, not gated).

```text
group,shape,op,baseline_median_ns,ring_median_ns,ring/baseline,control/baseline,ordereddict/baseline
counts,64x1,retire_tip,37518,227,0.006,1.000,
counts,64x1,finish_recovery,38652,6744,0.174,1.000,
counts,64x1,recover_leaf,38755,6818,0.176,1.000,
counts,64x4,retire_tip,40121,198,0.005,1.000,
counts,64x4,finish_recovery,158940,16728,0.105,1.000,
counts,64x4,recover_leaf,159073,16802,0.106,1.000,
counts,256x1,retire_tip,567534,227,0.000,1.000,
counts,256x1,finish_recovery,572124,26712,0.047,1.000,
counts,256x1,recover_leaf,572227,26786,0.047,1.000,
counts,256x4,retire_tip,578009,198,0.000,1.000,
counts,256x4,finish_recovery,2306652,66648,0.029,1.000,
counts,256x4,recover_leaf,2306785,66722,0.029,1.000,
counts,1024x1,retire_tip,8954478,227,0.000,1.000,
counts,1024x1,finish_recovery,8972892,106584,0.012,1.000,
counts,1024x1,recover_leaf,8972995,106658,0.012,1.000,
counts,1024x4,retire_tip,8996441,198,0.000,1.000,
counts,1024x4,finish_recovery,35965020,266328,0.007,1.000,
counts,1024x4,recover_leaf,35965153,266402,0.007,1.000,
e1,256x4,recover_leaf,7331771,238958,0.033,0.994,
e1,256x4,finish_recovery,7307000,237917,0.033,1.000,
e1,256x4,retire_tip,1844625,625,0.000,1.007,
e1,256x1,retire_tip,1709458,666,0.000,0.996,
e1,256x1,recover_leaf,1752312,80792,0.046,0.998,
e1,256x1,finish_recovery,1741062,80333,0.046,1.000,
e1,1024x4,recover_leaf,121336041,1009125,0.008,1.003,
e1,1024x4,retire_tip,30698271,1104,0.000,1.006,
e1,1024x4,finish_recovery,121319375,1011458,0.008,1.002,
e1,1024x1,recover_leaf,28181750,329833,0.012,1.002,
e1,1024x1,retire_tip,27834750,917,0.000,1.000,
e1,1024x1,finish_recovery,28216292,323583,0.011,0.996,
e1,64x4,retire_tip,120584,583,0.005,1.010,
e1,64x4,recover_leaf,509166,61208,0.120,1.019,
e1,64x4,finish_recovery,507667,59208,0.117,1.016,
e1,64x1,recover_leaf,121542,21667,0.178,0.998,
e1,64x1,finish_recovery,120208,21042,0.175,1.007,
e1,64x1,retire_tip,112084,708,0.006,1.000,
sweep,16x128,finish_recovery,5936125,432146,0.073,0.991,
sweep,16x128,retire_tip,49875,625,0.013,1.006,
sweep,1x128,retire_tip,2167,542,0.250,1.000,
sweep,1x128,finish_recovery,242584,25500,0.105,0.999,
sweep,8x64,finish_recovery,703000,104667,0.149,1.000,
sweep,8x64,retire_tip,10541,542,0.051,1.008,
sweep,2x1024,finish_recovery,29173791,448958,0.015,1.008,
sweep,2x1024,retire_tip,24708,583,0.024,0.988,
sweep,1x1024,finish_recovery,13514062,211749,0.016,0.992,
sweep,1x1024,retire_tip,11166,583,0.052,1.000,
sweep,32x128,retire_tip,157166,583,0.004,1.001,
sweep,32x128,finish_recovery,16444854,882916,0.054,1.019,
sweep,10x512,finish_recovery,46953917,1176291,0.025,1.000,
sweep,10x512,retire_tip,84916,625,0.007,0.998,
sweep,2x128,retire_tip,4042,583,0.144,0.990,
sweep,2x128,finish_recovery,518291,50917,0.098,0.993,
sweep,32x64,finish_recovery,4810708,430166,0.089,0.998,
sweep,32x64,retire_tip,87167,625,0.007,1.019,
sweep,8x256,finish_recovery,8997500,435958,0.048,0.990,
sweep,8x256,retire_tip,34250,625,0.018,0.999,
sweep,4x256,retire_tip,14333,584,0.041,1.009,
sweep,4x256,finish_recovery,4048250,212000,0.052,0.986,
sweep,10x128,retire_tip,25958,583,0.022,0.998,
sweep,10x128,finish_recovery,3170750,269833,0.085,0.999,
sweep,4x64,retire_tip,4667,542,0.116,0.991,
sweep,4x64,finish_recovery,308625,51292,0.166,1.000,
sweep,1x512,retire_tip,6000,584,0.097,1.000,
sweep,1x512,finish_recovery,3425166,104250,0.030,0.993,
sweep,2x256,retire_tip,6917,625,0.090,0.994,
sweep,2x256,finish_recovery,1895250,104083,0.055,1.002,
sweep,4x1024,finish_recovery,64366584,921875,0.014,0.999,
sweep,4x1024,retire_tip,52334,584,0.011,0.997,
sweep,2x64,retire_tip,2334,625,0.268,1.000,
sweep,2x64,finish_recovery,144209,26458,0.183,1.002,
sweep,32x512,finish_recovery,220929625,4071250,0.018,1.007,
sweep,32x512,retire_tip,518833,583,0.001,0.981,
sweep,4x512,finish_recovery,15464604,442729,0.029,0.999,
sweep,4x512,retire_tip,27584,583,0.021,0.985,
sweep,8x512,finish_recovery,35015333,886125,0.025,1.009,
sweep,8x512,retire_tip,62583,583,0.009,1.001,
sweep,9x1024,retire_tip,143042,583,0.004,0.999,
sweep,9x1024,finish_recovery,167481250,2140917,0.013,1.003,
sweep,32x256,retire_tip,292000,583,0.002,1.002,
sweep,32x256,finish_recovery,60002667,1835500,0.031,1.022,
sweep,8x128,retire_tip,19042,583,0.031,1.002,
sweep,8x128,finish_recovery,2429667,209958,0.086,1.002,
sweep,12x512,finish_recovery,56879917,1368375,0.024,0.996,
sweep,12x512,retire_tip,113000,583,0.005,0.968,
sweep,16x512,retire_tip,168250,542,0.003,1.000,
sweep,16x512,finish_recovery,85758458,1864459,0.022,0.979,
sweep,4x128,retire_tip,8334,584,0.070,0.995,
sweep,4x128,finish_recovery,1090833,104500,0.096,1.004,
sweep,32x1024,finish_recovery,905271125,11369917,0.013,0.993,
sweep,32x1024,retire_tip,1014666,584,0.001,0.995,
sweep,16x256,finish_recovery,22064333,875687,0.040,1.002,
sweep,16x256,retire_tip,91667,583,0.006,1.001,
sweep,12x1024,finish_recovery,228508917,2955583,0.013,1.008,
sweep,12x1024,retire_tip,222708,583,0.003,0.996,
sweep,10x1024,finish_recovery,192749375,2413167,0.013,0.996,
sweep,10x1024,retire_tip,170084,584,0.003,0.972,
sweep,8x1024,retire_tip,123625,625,0.005,1.008,
sweep,8x1024,finish_recovery,144161542,1851250,0.013,0.991,
sweep,9x128,retire_tip,21958,583,0.027,0.998,
sweep,9x128,finish_recovery,2800125,238542,0.085,1.001,
sweep,2x512,finish_recovery,7273729,213479,0.029,1.002,
sweep,2x512,retire_tip,12875,583,0.045,1.000,
sweep,16x64,finish_recovery,1722166,210291,0.122,1.002,
sweep,16x64,retire_tip,28709,542,0.019,0.980,
sweep,16x1024,finish_recovery,343642583,4501042,0.013,0.999,
sweep,16x1024,retire_tip,341542,583,0.002,0.988,
sweep,12x128,finish_recovery,3987625,323125,0.081,0.990,
sweep,12x128,retire_tip,33417,584,0.017,1.001,
sweep,1x64,finish_recovery,67750,13334,0.197,0.999,
sweep,1x64,retire_tip,1333,583,0.437,1.000,
sweep,9x512,finish_recovery,40758625,992417,0.024,1.002,
sweep,9x512,retire_tip,73291,584,0.008,1.002,
sweep,1x256,retire_tip,3500,583,0.167,0.988,
sweep,1x256,finish_recovery,915792,50625,0.055,0.999,
mixed,1x1024+512,retire_tip,6837792,625,0.000,1.004,
mixed,1x1024+512,finish_recovery,19731042,356375,0.018,1.003,
mixed,1x1024+64,finish_recovery,13538271,237500,0.018,1.000,
mixed,1x1024+64,retire_tip,129250,584,0.005,0.996,
mixed,1x1024+1000,finish_recovery,39053833,492084,0.013,1.000,
mixed,1x1024+1000,retire_tip,26336875,792,0.000,1.000,
launch,4x512,launched_tip,9791,500,0.051,1.004,
launch,4x512,launched_dead64,10417,1292,0.124,1.012,
launch,32x64,launched_dead64,2709,1292,0.477,1.000,
launch,32x64,launched_tip,1959,500,0.255,0.999,
launch,1x64,launched_tip,1292,500,0.387,1.000,
launch,1x64,launched_dead64,1917,1292,0.674,1.021,
launch,1x1024,launched_tip,15500,500,0.032,1.003,
launch,1x1024,launched_dead64,16167,1250,0.077,1.005,
launch,1024x1,launched_dead64,15500,1375,0.089,0.965,
launch,1024x1,launched_tip,13583,542,0.040,0.988,
frontiers,32x64,frontiers,9042,42333,4.682,0.995,
frontiers,1024x1,frontiers,54500,150792,2.767,0.995,
frontiers,4x512,frontiers,6000,37083,6.181,1.007,
frontiers,1x1024,frontiers,1917,16625,8.672,1.021,
frontiers,1x64,frontiers,375,1417,3.779,1.109,
payload,1024x1,payload,2867146,2991291,1.043,1.000,
payload,1x1024,payload,31211187,31327479,1.004,1.000,
payload,4x512,payload,34575083,34520500,0.998,1.000,
payload,32x64,payload,9239854,9266208,1.003,1.000,
payload,1x64,payload,292500,293625,1.004,1.002,
drain,chain1024,median,6167,667,0.108,0.973,
drain,chain1024,max,26125,48833,1.869,0.987,
drain,chain1024,total,6422576,817213,0.127,0.990,
drain,siblings1024,median,6910542,709,0.000,1.003,
drain,siblings1024,max,27846208,51084,0.002,1.015,
drain,siblings1024,total,9495198607,864212,0.000,1.002,
select,4x64,miss,741,564,0.761,1.018,1.532
select,2x64,miss,456,383,0.840,1.000,1.469
select,8x64,miss,1291,936,0.725,0.999,1.545
select,16x64,head,289,245,0.848,1.007,1.035
select,S1024,far,51238,33523,0.654,1.020,1.905
select,1x64,head,278,239,0.860,0.996,1.036
select,S256,head,312,255,0.817,1.003,1.010
select,32x64,miss,4545,3131,0.689,1.004,1.602
select,S64,far,3381,2261,0.669,1.023,1.765
select,2x64,head,285,247,0.867,1.011,1.063
select,1x64,miss,314,293,0.933,1.022,1.338
select,4x64,head,288,241,0.837,1.007,1.038
select,8x64,head,287,242,0.843,1.003,1.049
select,S1024,miss,143181,100006,0.698,0.990,1.662
select,S64,head,290,247,0.852,1.000,1.031
select,32x64,head,289,245,0.848,1.007,1.048
select,S1024,head,302,266,0.881,0.990,1.056
select,16x64,miss,2402,1684,0.701,0.993,1.550
select,S256,miss,35723,23141,0.648,1.007,1.577
select,S256,far,13345,8226,0.616,1.007,1.783
select,S64,miss,8811,5946,0.675,1.002,1.628
```

Acceptance against the Task 6 criteria:

- `retire`, `finish_recovery`, `recover`, `launched`: every shape at or
  under baseline; the largest ring/baseline per-shape median over all 135
  gated rows other than the payload proxy is 0.933 (a `select` row); the
  largest among the exit, recovery and launch rows is 0.674 (`launched`
  under 64 dead TIDs, 1x64). Met.
- Wide shapes: `retire` on 1,024 siblings 27.83 ms → 917 ns (about
  30,000×); `finish_recovery` on 1,024 × 4 121.3 ms → 1.011 ms (120×);
  `recover` of a leaf on 1,024 × 4 121.3 ms → 1.009 ms (120×). Met (≥ 100×).
- `select` head hit and full miss, every S and every chain count: ring
  0.648–0.933 of baseline. Met. Far-end hits (reported): 0.616–0.669. The
  OrderedDict arm (reported): 1.01–1.06 on head hits, 1.34–1.91 on far-end
  hits and full misses.
- Drain rows (reported, not gated): chain of 1,024, median 667 ns against
  6,167 ns, maximum single `retire` 48.8 µs against 26.1 µs (the maximum is
  a `_compact_up` call, register row 10); 1,024 siblings, median 709 ns
  against 6.91 ms, maximum 51.1 µs against 27.8 ms.
- `frontiers` alone (reported): 2.8–8.7× baseline (1.4 µs against 0.4 µs at
  1x64; 150.8 µs against 54.5 µs at 1,024 siblings), as E14 and E24
  predicted.
- **Payload proxy: not met on one shape.** Nine-round confirmation run:
  1x64 1.004, 1x1024 1.004, 4x512 0.998, 32x64 1.003, and **1024x1 (1,024
  siblings) 1.043**, per-round ring/baseline 1.023–1.064, against the
  control's 1.000 (per-round 0.975–1.016); the five-round run gave 1.040
  (per-round 1.024–1.057) against the control's 1.008. The increase,
  about 124 µs on a 2.87 ms proxy, is the `frontiers` derivation's increase
  on that shape (150.8 µs against 54.5 µs), as E24 predicted (4.1 %). So
  open item O2 is answered **no**, register row 9 is **D** on the 1,024-
  sibling shape, and under the Task 6 Stop-if the implementer did not tune
  and reports the numbers to the owner. The rest of Task 6 was completed.

Gates (all with the change applied, `BASE` = `b01b9269`):

- Per-task: `tests/core/test_family_scheduling.py` and
  `tests/core/test_work_conserving_admission.py`: 365 passed.
- Manager-level, SQLite (the §7 list): 65 passed, 0 failed.
- Manager-level, PostgreSQL (`./bin/pytest-pg` with the §7 list, which
  also includes `tests/core/test_family_scheduling.py` and
  `tests/system/test_measured_admission_pg.py`): `424 passed in 48.83s`.
- Full fast suite `./.venv/bin/python -m pytest -q -p no:cacheprovider`:
  exit 0; 6,365 passed, 47 skipped, 0 failed (counted from the progress
  line; this configuration prints no summary line under `-q`).
- `mypy` (the CI target list): `Success: no issues found in 483 source
  files`. `ruff check .`: `All checks passed!`. `ruff format --check` on
  the two touched Python files: `2 files already formatted` (the
  repository-wide `ruff format --check .` reports two Markdown plan files,
  `docs/plans/2026-11-02-tid-display-encoding-plan.md` and this plan's
  line 398 design block, both unchanged by this implementation, so not a
  regression). `./bin/check-doc-paths`: `check-doc-paths: OK`. Plan
  metadata and spec hygiene tests: passed.
- `git diff --stat "$BASE" -- weft/core/manager.py`: empty.
- `pytest -m ""` (all tests including slow) was not run; the brief's gate
  is the fast suite.

Traceability: `grep -n "MA-1.8" weft/core/family_scheduling.py` shows the
module docstring (line 4), the class docstring `Spec: [MA-1.8]` (line 233)
and `_find`'s `Spec: [MA-1.8] ("Cache contracted scheduling-parent
pointers")` (line 303); the new `_Implementation mapping_` bullet and both
`## Related Plans` backlinks are in place.

Not done by the implementer (owed before closeout): the [WT-7] keyed
possession probe (the owner writes the key; a non-authoring agent of a
different family answers), the completed-work cross-family review, the
theory commit (Owner Decision 1), and the owner's ruling on the register
row 9 diminution. The 4.2-a audit-table row was not edited (the owner did
not ask); its status: the frontier rebuild is now the textbook union-find
plus round-robin ring, pending the row 9 ruling. Plan `Status:` stays
`draft` (the closeout flip to `completed` waits on those items; `promoted`
would make `bin/check-doc-paths` require the unapplied theory blocks).

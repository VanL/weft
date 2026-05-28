# Documentation Clarity And Plan Curation Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [OBS.13]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.3]; docs/specifications/03-Manager_Architecture.md [MA-1]; docs/specifications/09-Implementation_Plan.md [IP-1.0]; docs/plans/README.md
Superseded by: none

## Goal

Move Weft from "very well documented" to "excellent" by closing the documentation
structural gaps and plan-corpus drift that the 2026-05-28 codebase review
identified. The work has four pillars:

1. Make the densest specs navigable (top-of-file TOCs; OBS.13 reorganized into
   sub-invariants without breaking existing references).
2. Make the largest cohesive runtime modules/classes (`Manager`, `TaskMonitor`,
   `MonitorStore`) navigable from a cold read by adding in-file section maps.
3. Pin documentation invariants in code where the existing test guards have
   gaps (monitor justification paragraph; monitor-not-truth import boundary).
4. Curate the plan corpus: classify every plan as `keep`, `mark-completed`,
   `mark-superseded`, or `retire`, and act on the classifications.

The work changes **no runtime behavior**, **no public CLI shape**, **no queue
names**, **no TaskSpec schema**, **no result payloads**, and **no spec section
identifiers that are already referenced from shipped code**.

## Source Documents

Read these before implementation. They are not optional.

- `AGENTS.md` (project conventions, file layout, the "size is not a smell"
  philosophy, and the warning that the densest specs are deliberately dense).
- `docs/agent-context/README.md` (shared agent context entry point).
- `docs/agent-context/decision-hierarchy.md` (spec > plan > local prompt).
- `docs/agent-context/principles.md` (collaboration, change hygiene, document
  traceability rules).
- `docs/agent-context/engineering-principles.md` (extension over invention;
  "real broker and process tests beat mock-heavy tests"; the warning signs
  about second code paths).
- `docs/agent-context/runbooks/writing-plans.md` (audience assumptions and the
  required plan sections).
- `docs/agent-context/runbooks/hardening-plans.md` (one-way doors and the
  rollback-first discipline that applies to plan deletion).
- `docs/agent-context/runbooks/runtime-and-context-patterns.md` §2 (Queue handle
  preference rule — referenced by the edge-case comment template in Task 6).
- `docs/agent-context/lessons.md` and `docs/lessons.md` (durable corrections).
- `docs/specifications/07-System_Invariants.md` (especially OBS.13 in full).
- `docs/specifications/05-Message_Flow_and_State.md` (all sections).
- `docs/specifications/01-Core_Components.md` (CC-2 family).
- `docs/specifications/03-Manager_Architecture.md` (MA-1 family).
- `docs/specifications/09-Implementation_Plan.md` (Boundary Inventory and
  Pure Decision Seams sections added by the 2026-05-28 cleanup plan).
- `docs/plans/README.md` (curation policy and status taxonomy).
- `docs/plans/2026-05-28-codebase-excellence-cleanup-plan.md` (immediate
  predecessor of this work; this plan continues that hygiene push, it does
  not contradict it).

Comprehension questions the implementer must answer before editing:

1. Why are `Manager`, `TaskMonitor`, `MonitorStore`, and `_constants.py`
   allowed to be large in this repo? (If the answer is not "their concerns
   share state and must coordinate", reread AGENTS.md §1.1.)
2. Which document is normative when a spec and a plan disagree? (Spec.)
3. What does `[OBS.13]` reference today — a single invariant, a section, or
   both? (Both — it is one bullet that bundles ~12 distinct rules. The split
   in Task 4 keeps `[OBS.13]` resolvable as the section umbrella so existing
   references continue to be valid.)
4. What is the curation policy in `docs/plans/README.md` for removal?
   ("Remove plans that were superseded, exploratory only, audit-only,
   roadmap-only, process-only, or not implemented as written.")
5. Why is plan deletion a one-way door even though git preserves history?
   (Because the README index, spec backlinks, and any cross-plan references
   must be updated atomically with the deletion or the corpus is broken.)

If you cannot answer those from the documents above, stop and reread before
touching code or docs.

## Current Structure Snapshot

Sized today (2026-05-28):

- `docs/specifications/05-Message_Flow_and_State.md`: 1,114 lines, 21 H2/H3
  sections, no top-of-file TOC.
- `docs/specifications/07-System_Invariants.md`: 634 lines, 19 OBS.* invariants,
  no top-of-file TOC.
- `docs/specifications/01-Core_Components.md`: 569 lines, no TOC.
- `docs/specifications/03-Manager_Architecture.md`: 424 lines, has a brief
  navigation section already.
- OBS.13 alone is **94 lines** of a single bullet point (between OBS.13 and
  OBS.14 markers). The cleanup plan added the "Dealing with processes can be
  messy" justification inside it without restructuring; the rest still bundles
  ~12 distinct rules.
- `weft/core/manager.py`: 6,693 lines; the `Manager` class itself opens at
  line 286 and runs for ~6,407 lines.
- `weft/core/monitor/task_monitor.py`: 4,250 lines.
- `weft/core/monitor/store.py`: 2,307 lines.
- `docs/plans/`: 129 plan files including this plan. 125 currently
  `completed` and 4 currently `draft`. The README plan-count footer is
  synchronized today (`129`), but it must be refreshed after any Task 9
  removals.
- `tests/specs/test_spec_hygiene.py`: 4 hygiene checks (monitor justification
  paragraph, OBS.13 sub-invariant decomposition, status-sensitive plan wording,
  Django spec citation).
- `tests/architecture/test_import_boundaries.py`: enforces
  `cli -> commands -> core` one-way layering and pins the monitor-derived
  evidence boundary. Current code intentionally allows
  `weft/commands/tasks.py` to consult Monitor store as a derived status
  fallback after raw task-log retirement; Task 8 must not encode an import ban
  that contradicts that behavior.

OBS.13 reference footprint (do not break this):

```bash
rg -n "\[OBS\.13\]" weft/ docs/ | wc -l
# returns 179 today, because this plan itself contains many references
# returns 148 today if this plan file is excluded
```

Do not use the raw count as a correctness proof. This plan and its
implementation record may add `[OBS.13]` references. The correctness rule is
that every pre-existing `[OBS.13]` reference still resolves because the
umbrella `**OBS.13**` marker remains, and the new `**OBS.13.x**` sub-invariants
resolve after Task 4.

Direct `Queue(...)` constructions:

- `weft/context.py:139` is `WeftContext.queue()` itself.
- `weft/core/tasks/base.py:355` is the task `_queue()` helper itself.
- `weft/commands/interactive.py:73`, `weft/core/spawn_requests.py:76/220/251`,
  `weft/core/tasks/multiqueue_watcher.py:160/200/313`, and
  `weft/core/manager.py:2255/2388/3021/3120/3465` all have short-lived or
  edge-case comments in the current tree. Task 6 is now an audit/preservation
  task, not a known missing-comment implementation task.

Module docstrings touched by the cleanup plan:

- `weft/core/monitor/task_monitor.py:7-8` references `[OBS.13]` inline (per plan).
- `weft/core/monitor/store.py:6-8` now says the justification lives at
  `[OBS.13]` and that the store remains operational evidence rather than
  task-state truth. Task 5 is now a preservation/verification task.

## Invariants and Constraints

These apply to the whole plan.

- **Specs stay normative.** Plans are non-normative. This plan does not change
  what Weft does, only how the documentation describes it.
- **Existing spec section identifiers must keep resolving.** Pre-plan
  `[OBS.13]` references stay valid; the split in Task 4 is additive
  (`[OBS.13]` becomes the umbrella section header, sub-rules become
  `[OBS.13.1]`, `[OBS.13.2]`, etc., under it).
- **No runtime behavior change.** No queue contract change. No state transition
  change. No reserved policy change. No `spec`/`io` mutation. No
  `weft.state.*` promotion to product persistence.
- **No new dependencies, no new abstractions.** This plan does not introduce
  a documentation generator, a spec linter framework, or a plan-management
  CLI. It uses Markdown, file paths, and `rg`.
- **No drive-by refactor.** Adding TOCs and section-map comments is the only
  in-file structural change permitted in source modules. Function bodies,
  method signatures, and class structure must not move.
- **The monitor justification paragraph stays present.** Task 7 adds a guard
  for this; the paragraph wording at OBS.13 must continue to contain the
  string `Dealing with processes can be messy`.
- **The store.py docstring must continue to describe operational-not-truth
  semantics.** Task 5's edit must not weaken that line.
- **Plan deletion is a one-way door.** Even though `git log` preserves
  content, plan deletion changes the corpus index, breaks any incoming
  Markdown links, and removes context for future agents. Task 9 must:
  - hold every removal candidate to a higher review bar (external review
    required before `git rm`),
  - prefer status changes (`completed`, `superseded by`) over removal when
    the plan still has explanatory value,
  - update the README index and any spec backlinks in the same change as
    the removal.
- **TDD where practical, narrow guards where not.** Task 4 gets a failing
  OBS.13 decomposition guard before the spec split, Task 8 gets a focused
  import-boundary red-state check, and Task 7 preserves the already-landed
  monitor paragraph guard. TOC additions and section-map comments get manual
  verification because their value is human/agent readability, not
  machine-checkable behavior.
- **No new policy in `_constants.py`.** This plan does not introduce new
  thresholds, queue names, or classification values.
- **No new test mocks of core broker/process behavior.** Task 8 may use an
  import-graph assertion for result/client boundaries, but it must not ban the
  current `weft/commands/tasks.py` Monitor-store derived-status fallback.

Stop and re-plan if:

- a TOC addition wants to renumber or rename existing section codes,
- the OBS.13 split would invalidate any existing `[OBS.13]` reference,
- a section-map comment in `Manager` or `TaskMonitor` would require moving
  methods around to make line ranges contiguous,
- plan curation discovers a plan whose status cannot be classified from
  code/spec evidence (in which case: leave as draft and note the ambiguity,
  do not guess),
- a documentation drift guard would require touching the spec it guards
  (i.e., circular logic — the guard should fail today against the current
  spec and pass after the spec content is verified, not after the spec
  is edited).

## Rollout and Rollback

This is a documentation-and-test plan. Rollback is trivial per slice:

- TOC additions and section-map comments: revert the file change.
- OBS.13 split (Task 4): revert the spec change. Sub-invariant references
  added during this plan must be removed in the same revert; otherwise
  shipped docstrings will point to identifiers that no longer exist.
- store.py docstring fix (Task 5): revert the file change.
- Queue-site comments (Task 6): revert the file change.
- Spec hygiene guard (Task 7) and architecture test (Task 8): revert the
  test file change; the underlying documented invariants remain.
- Plan-corpus changes (Task 9): each removal is a separate commit; revert
  the commit. **Do not batch multiple removals into one commit** — the
  whole point of treating deletion as a one-way door is per-decision
  reversibility.

There is no runtime rollout sequence. Tests must stay green at every slice
boundary.

## Out of Scope

- Splitting `Manager`, `TaskMonitor`, `MonitorStore`, `TaskSpec`, or
  `_constants.py` into smaller files.
- Renaming any spec section code (`[MF-*]`, `[OBS.*]`, `[CC-*]`, etc.) that
  is referenced from shipped code or other specs.
- Rewriting historical plans, even badly written ones. Curation in Task 9 is
  status classification and optional removal, not editorial revision.
- Creating a new `docs/lessons-summary.md`, `docs/architecture.md`, or any
  other meta-document that would compete with the specs.
- Adding a documentation generator, a Markdown lint framework, or a
  spec-to-code crosswalk generator.
- Adding test guards for hygiene items the 2026-05-28 plan deliberately
  left as grep-only (bare `except Exception:` annotation, `time.sleep`
  literal absence, direct `Queue(...)` comment presence). Those remain
  grep-verified; this plan adds machine guards only for the two highest-
  value documentation invariants (monitor paragraph, monitor-not-truth).
- Connecting the `connect()` API redundancy noted in the 2026-05-28 review.
  That is API design and belongs in its own plan.
- Postgres-backend or backend-neutral test coverage changes.
- Plan-format changes to `docs/agent-context/runbooks/writing-plans.md`.

## Task 1: Add Top-of-File Table of Contents to the Largest Specs

Outcome:
Each spec over ~400 lines opens with a TOC that lets an agent or human jump
to a section by anchor without scrolling. The TOC is generated by reading the
existing `##` and `###` headers — no section is renamed, renumbered, or moved.

Files to touch:

- `docs/specifications/05-Message_Flow_and_State.md` (1,114 lines — highest
  priority).
- `docs/specifications/07-System_Invariants.md` (634 lines).
- `docs/specifications/01-Core_Components.md` (569 lines).
- `docs/specifications/03-Manager_Architecture.md` (424 lines — has a brief
  nav section; reformat as a TOC for consistency).
- Optionally `docs/specifications/09-Implementation_Plan.md` if it crosses
  400 lines after the Boundary Inventory and parity matrix additions.

Read first:

- The spec being TOC'd (read it end-to-end, not just the headers).
- `docs/specifications/00-Quick_Reference.md` for the navigation conventions
  the rest of the spec corpus already uses.

Approach:

1. After the level-1 title and any `See also:` block, insert:

   ```markdown
   ## Table of Contents

   - [Section Name](#section-name)
     - [Subsection Name](#subsection-name)
   - …
   ```

2. Use only the section headers that already exist. Do not invent grouping
   levels.
3. Anchors are GitHub-flavored Markdown auto-anchors (lowercase, dashes for
   spaces, strip punctuation). Do not hand-author anchor IDs.
4. For 07-System_Invariants.md specifically: list every OBS.* / STATE.* /
   QUEUE.* / etc. group at the H3 level, but **do not** list individual
   invariants. The TOC is for navigation, not invariant enumeration.
5. Keep TOC entries terse. No descriptions, no parenthetical hints.

Red-green TDD:

- Not practical (Markdown TOC is a navigation aid, not behavior). Manual
  proof: pick three random section codes from each spec and confirm the
  TOC link reaches the section in a Markdown preview or GitHub render.

Tests:

```bash
./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py
```

(The plan-metadata test should remain green; the TOC additions do not change
spec section identifiers and so cannot affect plan backlinks. Run it anyway
as a sanity gate.)

Do not:

- renumber sections,
- rename sections,
- add depth that does not already exist in the spec,
- add a TOC to specs already under ~400 lines.

Done when:

- Each listed spec has a TOC immediately after the title and `See also:` block.
- Every TOC link resolves on render.
- `git diff` shows only TOC/navigation changes before the first substantive
  existing section in each file.

## Task 2: Add a Section Map to the `Manager` Class

Outcome:
A cold reader of `weft/core/manager.py` can find the major method clusters
(reactor loop, registry/leadership, spawn dispatch, child supervision,
service convergence, autostart, control handling, shutdown) without grepping.

Files to touch:

- `weft/core/manager.py`.

Read first:

- `docs/specifications/03-Manager_Architecture.md` [MA-1.1] through [MA-1.7]
  (the implementation mapping bullets — they already enumerate the method
  clusters this task will document).
- `weft/core/manager.py` lines 286–end (skim the method list with
  `rg -n "^    def " weft/core/manager.py` to confirm the cluster boundaries
  match what is already in [MA-1.*]).

Approach:

1. Immediately after the `Manager(ServiceTask):` class docstring (which today
   ends around line 295), insert a section-map comment block:

   ```python
   # --- Section map ----------------------------------------------------------
   # Section map (line ranges are approximate; trust the spec mapping over
   # the line numbers if they diverge):
   #
   #   1. __init__ and bootstrap           - see [MA-1.1]
   #   2. Reactor loop and idle timing     - see [MA-1.5], [MA-1.7]
   #   3. Registry heartbeat and leadership - see [MA-1.4]
   #   4. Spawn dispatch and reservation   - see [MA-1.1]
   #   5. Child launch and inbox seeding   - see [MA-1.2], [MA-1.3]
   #   6. Child supervision and reaping    - see [MA-1.5]
   #   7. Service convergence/autostart    - see [MA-1.6], [MA-1.6a]
   #   8. Control handling                 - see [MA-1.7]
   #   9. Shutdown and unregister          - see [MA-1.4]
   #
   # When in doubt about ownership, the spec mapping in 03-Manager_Architecture.md
   # is normative; line ranges in this file are not.
   # -------------------------------------------------------------------------
   ```

   Adjust the cluster list to match the actual MA-1.* sub-codes that exist in
   `03-Manager_Architecture.md` after your read; do not invent codes.

2. Do not insert per-cluster divider comments inside the class body in this
   slice. The top-of-class map is the minimum useful change; per-cluster
   dividers can be a follow-up if a future plan finds them necessary.
3. The section-map comment refers to spec codes, not line numbers, precisely
   so it does not rot when methods move.

Red-green TDD:

- Not practical. The proof is qualitative: a new agent (or the implementer
  themselves, simulating a cold read) can answer "where is leadership yield
  handled?" by reading the section map and following the spec code, without
  grepping the whole file.

Tests:

```bash
./.venv/bin/ruff check weft/core/manager.py
./.venv/bin/mypy weft/core/manager.py
./.venv/bin/python -m pytest -q tests/core/test_manager.py
```

Do not:

- move any method,
- change any method signature,
- add line numbers to the section map (they will rot),
- add doc comments to individual methods unless they are already missing a
  `Spec:` reference that should exist per AGENTS.md §4.5,
- expand the comment into a mini-spec — the spec already lives in 03-Manager_Architecture.md.

Done when:

- The section-map comment is in place immediately after the class docstring.
- Every cluster name resolves to a real `[MA-*]` code that exists in
  `03-Manager_Architecture.md`.
- ruff, mypy, and `tests/core/test_manager.py` are green.

## Task 3: Add Section Maps to `TaskMonitor` and `MonitorStore`

Outcome:
Cold readers of `weft/core/monitor/task_monitor.py` and
`weft/core/monitor/store.py` can find the major behavioral clusters and the
OBS.13 sub-rules each cluster implements.

Files to touch:

- `weft/core/monitor/task_monitor.py`.
- `weft/core/monitor/store.py`.

Read first:

- `docs/specifications/07-System_Invariants.md` OBS.13 (the full block).
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5].
- `weft/core/monitor/task_monitor.py` — confirm the major clusters
  (reactor / cleanup-cycle worker dispatch / runtime cleanup worker /
  cleanup policy selection / PONG handling / external sink).
- `weft/core/monitor/store.py` — confirm the major clusters (schema
  migration / collation merge / family disposition / raw-deletion
  reconciliation / orphan recovery / reserved cleanup proof).
- Task 4 of this plan (the OBS.13 split): if Task 4 has landed, refer to
  the sub-invariant codes; if Task 4 is pending, refer to `[OBS.13]` as a
  single section and add the sub-code links in a follow-up commit after
  Task 4.

Approach:

1. For `task_monitor.py`: insert a section-map comment after the class
   docstring of the primary task-monitor class (locate via
   `rg -n "^class " weft/core/monitor/task_monitor.py`). The block names
   each cluster and the OBS.13 sub-rule it implements:

   ```python
   # --- Section map ----------------------------------------------------------
   # Operational evidence only; lifecycle truth lives in
   # weft.log.tasks and task-local queues (see [OBS.13]).
   #
   #   1. Reactor and tick scheduling     - see [OBS.13.10]
   #   2. Cleanup cycle dispatch          - see [OBS.13.3]
   #   3. Runtime cleanup worker lane     - see [OBS.13.9]
   #   4. Cleanup policy selection        - see [OBS.13.7]
   #   5. PONG diagnostics                - see [OBS.13.11]
   #   6. External sink emission          - see [OBS.13.8]
   #
   # All cleanup effects are exact, policy-selected, and retryable.
   # -------------------------------------------------------------------------
   ```

   Use the actual OBS.13.* codes that exist after Task 4 lands; if Task 4 is
   not yet done, use `[OBS.13]` for all entries and add the sub-codes in a
   follow-up commit referenced in the implementation record below.

2. For `store.py`: insert a section-map comment after the module docstring
   (the module is layered enough that there is not one canonical class to
   anchor on; the module-level map is the right anchor):

   ```python
   # --- Section map ----------------------------------------------------------
   # Derived operational state, not lifecycle truth.
   #
   #   1. Schema initialization/migration - see [OBS.13.1]
   #   2. Collation merge                 - see [OBS.13.3]
   #   3. Family disposition state        - see [OBS.13.4]
   #   4. Raw-deletion reconciliation     - see [OBS.13.3]
   #   5. Orphan recovery/retirement      - see [OBS.13.4]
   #   6. Reserved cleanup proof          - see [OBS.13.5]
   #
   # Read [OBS.13] in 07-System_Invariants.md before changing schema, deletion,
   # or disposition rules; the spec is normative.
   # -------------------------------------------------------------------------
   ```

3. Same constraints as Task 2: no method moves, no signature changes, no
   line numbers in the map.

Red-green TDD:

- Not practical. Same qualitative proof as Task 2.

Tests:

```bash
./.venv/bin/ruff check weft/core/monitor
./.venv/bin/mypy weft/core/monitor
./.venv/bin/python -m pytest -q -n0 \
  tests/core/test_monitor_store.py \
  tests/core/test_monitor_external_log.py \
  tests/tasks/test_task_monitor.py
```

Do not:

- create a second "monitor architecture" document outside the spec,
- attach per-method spec refs that are not already required by AGENTS.md §4.5,
- expand the map past one screen.

Done when:

- Both files have a section-map comment immediately after their class or
  module docstring.
- Every referenced OBS.13.* code resolves to a real sub-invariant in
  `07-System_Invariants.md` (after Task 4) or to `[OBS.13]` umbrella
  (before Task 4).
- Tests above are green.

## Task 4: Split OBS.13 Into Sub-Invariants (Additive)

Outcome:
`[OBS.13]` becomes the umbrella header for a sub-invariant block. Each of the
12 conceptual rules currently bundled inside the 94-line bullet becomes a
short, focused `[OBS.13.x]` sub-invariant with its own line in the spec.
**Every pre-plan `[OBS.13]` reference continues to resolve** because the
umbrella section header keeps that identifier.

Files to touch:

- `docs/specifications/07-System_Invariants.md`.

Read first:

- The full OBS.13 block, end to end. Print it out if necessary; do not edit
  from memory.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (much of OBS.13
  is implementation detail of MF-5; ensure the spec mapping stays coherent
  between the two files).
- The 2026-05-28 cleanup plan's Task 12.4 (the "Dealing with processes can
  be messy" paragraph is part of the OBS.13 block and must remain).

Comprehension questions:

- Why does `[OBS.13]` need to stay resolvable as a section identifier?
  (Because existing references — code docstrings, spec mappings, plan backlinks
  — point at it. Renaming or removing it is a breaking change to the
  documentation contract.)
- What is the difference between an OBS.13 sub-rule that talks about
  *what may be deleted* and one that talks about *the ordering of cleanup
  effects*? (The first is a scope/eligibility rule; the second is a sequencing
  rule. They are independent — sub-invariant splitting groups them
  separately.)

Approach:

1. **Draft the split before editing the spec.** In a scratch space (a comment
   on this plan's implementation record, or a sibling Markdown file under
   `docs/plans/scratch/` that is *not* checked in), list each of the 12
   conceptual rules currently in OBS.13. For each, propose a sub-invariant
   ID and a one-sentence statement. Suggested decomposition (verify against
   the actual OBS.13 text — do not blindly accept this list):

   - **OBS.13.1** — Monitor-owned tables (`weft_monitor_meta`,
     `weft_monitor_task_collations`, `weft_monitor_task_messages`) are
     derived operational state; Monitor may create, verify, and additively
     migrate only those tables inside an already initialized Weft broker
     database.
   - **OBS.13.2** — Why the monitor exists: process cleanup is messy, partial,
     and retryable; the monitor exists to make that cleanup bounded and
     observable while keeping lifecycle truth in task-owned queues and logs.
     (This is the "Dealing with processes can be messy" paragraph — keep its
     exact wording so the Task 7 drift guard still matches.)
   - **OBS.13.3** — Retained `weft.log.tasks` cleanup path: malformed rows
     deleted; valid rows folded into Monitor tables before exact deletion;
     pending child refs physically removed after raw deletion succeeds.
   - **OBS.13.4** — Family disposition and parent retirement: terminal
     disposition records retryable state; parents retired only after raw
     deletion, summary, disposition, control cleanup, and any required
     reserved-cleanup proof have all been recorded.
   - **OBS.13.5** — Reserved cleanup proof: required only for rows with
     `reserved_probe_needed`; recorded as `reserved_cleanup_checked_at_ns`;
     deletion uses public SimpleBroker queue APIs.
   - **OBS.13.6** — Runtime-state queue cleanup: policy-driven; malformed
     rows deletable only from Weft-owned schema queues whose policy says
     malformed rows are disposable.
   - **OBS.13.7** — Cleanup exclusions: monitor must not delete active work,
     ambiguous task-local evidence, claimed outbox residue, user payload
     rows, unknown rows outside an explicit cleanup policy, inbox/reserved
     work without terminal task-log proof for the same TID, or non-exact
     lifecycle evidence.
   - **OBS.13.8** — Collation summary classification: user-task rows use
     `collation_kind=user_task`; service rows use a service classification
     and `record_type=service_summary`; external collated JSONL keeps the
     `task_log_collated` record type for compatibility.
   - **OBS.13.9** — Task-local queue cleanup at terminal disposition:
     standard `T{tid}.ctrl_in`, `T{tid}.ctrl_out`, and `T{tid}.inbox` are
     stale at terminal cleanup time; standard `T{tid}.outbox` retained until
     task-log retention age; standard `T{tid}.reserved` owned by the reserved
     cleanup policy.
   - **OBS.13.10** — Worker lane allocation: built-in task-log cleanup and
     runtime cleanup are the only TaskMonitor worker lanes that may own
     broker/store cleanup effects; the reactor must continue servicing
     task-local control while either lane is in flight; runtime cleanup
     must run as bounded, discrete worker slices.
   - **OBS.13.11** — PONG cleanup diagnostics: cached from the last cleanup
     cycle; may report queue-level and policy-level stats including zero-
     selected rows; must not perform queue scans, open external log files,
     query the Monitor store, recompute cleanup candidates, or delete/
     report rows while answering a liveness request.
   - **OBS.13.12** — The five top-level cleanup policy identities:
     `task_log.retention`, `monitor_store.lifecycle`,
     `task_local.terminal_runtime`, `task_local.dead_tid`,
     `runtime_state.retention`. Each policy run must remain bounded and
     report base/waypoint/blocked status.

2. **Write the split.** In `07-System_Invariants.md`, restructure the OBS.13
   block as follows:

   ```markdown
   - **OBS.13**: Monitor outputs (task monitor logs, checkpoints, runtime-prune
     reports, retention-prune reports/archives, collation tables, processor
     results) are operational evidence only. They must not become task lifecycle
     truth, status authority, or result authority. The sub-invariants below
     specify what the manager-supervised `TaskMonitor` and its collation store
     may and must not do; together they define the operational contract.

     - **OBS.13.1**: <one focused sentence>
     - **OBS.13.2**: <one focused paragraph including the "Dealing with
       processes can be messy" justification — keep that exact phrase>
     - … (and so on through OBS.13.12)
   ```

3. **Critical: keep the "Dealing with processes can be messy" phrase intact**
   in OBS.13.2. The Task 7 drift guard depends on it, and the 2026-05-28
   cleanup plan's acceptance criterion (`rg "Dealing with processes can be
   messy" docs/specifications/07-System_Invariants.md`) must continue to
   match.
4. **Do not migrate existing `[OBS.13]` references** in code or other specs
   to sub-codes in this slice. The split is additive; future work may
   migrate specific references if it improves clarity, but doing it in this
   slice would balloon scope.
5. Update the OBS.13 entries in spec's Related Plans / Related Documents
   sections only if a backlink wording becomes inaccurate.

Red-green TDD:

- Practical. Add to `tests/specs/test_spec_hygiene.py` before editing the spec:

  ```python
  def test_obs13_is_decomposed_into_sub_invariants() -> None:
      """OBS.13 must define at least one sub-invariant (OBS.13.x)."""

      text = (SPEC_DIR / "07-System_Invariants.md").read_text(encoding="utf-8")
      assert "**OBS.13.1**" in text, (
          "OBS.13 must be decomposed into sub-invariants (OBS.13.1, ...)"
      )
  ```

  This test should fail today (no OBS.13.x exists) and pass after the split.

Tests:

```bash
./.venv/bin/python -m pytest -q tests/specs/test_spec_hygiene.py
./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py
./.venv/bin/python -m pytest -q tests/architecture/test_import_boundaries.py
rg -n "\*\*OBS\.13\*\*" docs/specifications/07-System_Invariants.md
rg -n "\*\*OBS\.13\.1\*\*" docs/specifications/07-System_Invariants.md
```

Stop and re-plan if:

- the proposed split has fewer than 6 or more than 16 sub-invariants
  (either is a sign the granularity is wrong),
- splitting requires moving rules into a different OBS.* number,
- the "Dealing with processes can be messy" paragraph would have to be
  reworded to fit the sub-invariant shape,
- the resulting OBS.13.x bullet count exceeds 1.5x the original line count.

Do not:

- rename OBS.13 itself,
- migrate existing `[OBS.13]` references in this slice,
- introduce OBS.14+ identifiers (those are taken by existing invariants;
  splitting OBS.13 must stay inside the OBS.13.* namespace),
- summarize away nuance to make sub-invariants short — keep the substantive
  rules; only the bullet structure changes.

Done when:

- `tests/specs/test_spec_hygiene.py` passes its new `test_obs13_is_decomposed_into_sub_invariants`.
- `rg "Dealing with processes can be messy" docs/specifications/07-System_Invariants.md`
  still returns a hit.
- `rg -n "\*\*OBS\.13\*\*" docs/specifications/07-System_Invariants.md`
  returns the umbrella marker, and `**OBS.13.1**` through the final introduced
  subcode all resolve.
- The OBS.13 block in `07-System_Invariants.md` is now an umbrella header
  followed by 6–16 sub-invariant bullets, each no more than 10 lines.
- A reader searching for "what may the monitor delete?" can find the answer
  by jumping to OBS.13.7 without reading 94 lines of prose.

## Task 5: Preserve `store.py` Module Docstring [OBS.13] Reference

Outcome:
The new sentence added by the 2026-05-28 cleanup plan to
`weft/core/monitor/store.py` explicitly names `[OBS.13]`, matching the
plan's Task 12.4 requirement. The current tree already satisfies this; this
task verifies it and keeps it aligned if Task 4 introduces OBS.13 subcodes.

Files to touch:

- Usually none. Touch `weft/core/monitor/store.py` only if Task 4's OBS.13
  split requires a more precise subcode reference.

Read first:

- `docs/plans/2026-05-28-codebase-excellence-cleanup-plan.md` Task 12.4
  (the exact requirement text).
- Current `weft/core/monitor/store.py:1-15`.

Approach:

1. Confirm the module docstring states both:
   - the justification lives at `[OBS.13]`;
   - the store is operational evidence, not task-state truth.
2. If Task 4 introduces a more precise `[OBS.13.x]` code for Monitor-owned
   tables, it is acceptable to add that subcode alongside `[OBS.13]`, but do
   not remove the umbrella reference.
3. The "Spec references" block below is unaffected unless Task 4's new
   subcode materially improves the mapping.

Red-green TDD:

- Not practical for a single docstring line. Manual verification:

  ```bash
  rg -n "\[OBS\.13\].*operational evidence|operational evidence.*\[OBS\.13\]" weft/core/monitor/store.py
  ```

  should return one hit after the edit.

Tests:

```bash
./.venv/bin/ruff check weft/core/monitor/store.py
./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py
```

Done when:

- `store.py` docstring's new sentence explicitly names `[OBS.13]` and
  operational-evidence semantics.
- Tests above remain green.

## Task 6: Audit Direct `Queue(...)` Edge Comments

Outcome:
Every direct `Queue(...)` construction in `weft/` outside queue-helper
implementations carries an explicit nearby comment explaining why a helper was
not used. This preserves the 2026-05-28 cleanup plan's intended
(but narrowly-grep'd) coverage without rewiring queue ownership.

Files to touch:

- Usually none. The current tree already annotates the known edge sites.
- If the audit finds a new unannotated direct site, touch only that file.

Read first:

- `docs/agent-context/runbooks/runtime-and-context-patterns.md` §2 (the
  "Reuse Queue Handles on Live Task Paths" rule).
- The function bodies surrounding any unannotated Queue site.

Approach:

1. Run:

   ```bash
   rg -n "Queue\(" weft --glob "*.py"
   ```

2. Exclude helper implementations and non-broker queue constructors:
   `WeftContext.queue()`, `BaseTask._queue()`, `queue.Queue`,
   `thread_queue.Queue`, and multiprocessing context queues.
3. For each remaining SimpleBroker `Queue(...)` site, confirm there is a nearby
   comment explaining the edge:
   - pre-context utility;
   - watcher-owned queue handle;
   - manager short-lived child/control probe handle;
   - CLI adapter constructed from explicit queue names.
4. If any site lacks a comment, add the smallest precise comment immediately
   above the `Queue(` call. If a helper is actually available and the direct
   call is not an edge, stop and record a follow-up plan candidate; do not
   silently rewire the call in this documentation slice.

Red-green TDD:

- Practical at the grep level. The audit command is:

  ```bash
  rg -n "Queue\(" weft --glob "*.py"
  ```

  The proof is the implementation record's short list of direct SimpleBroker
  sites and the reason each is acceptable. A new unannotated site is the red
  state; adding the comment or filing a follow-up plan is green.

Tests:

```bash
./.venv/bin/ruff check weft/core/manager.py weft/core/spawn_requests.py weft/core/tasks/multiqueue_watcher.py weft/commands/interactive.py
./.venv/bin/mypy weft/core/manager.py weft/core/spawn_requests.py weft/core/tasks/multiqueue_watcher.py weft/commands/interactive.py
```

(No new functional test; the change is comment/audit-only.)

Do not:

- replace any direct `Queue(...)` with a helper call,
- thread `WeftContext` deeper into the call stack,
- treat `WeftContext.queue()` or `BaseTask._queue()` as violations; they are
  the helper implementations.

Done when:

- The implementation record lists every direct SimpleBroker Queue site outside
  helper implementations and the accepted edge reason.
- Any diff for this slice is comments-only.
- ruff and mypy stay green.

## Task 7: Preserve the Monitor Justification Drift Guard

Outcome:
The 2026-05-28 cleanup plan's acceptance criterion for OBS.13.2 (the
"Dealing with processes can be messy" paragraph) remains locked in by the
focused assertion in `tests/specs/test_spec_hygiene.py`. Future drift —
removing the paragraph or rewriting it without that phrase — fails CI.

Files to touch:

- Usually none. The guard already exists in the current tree.
- If Task 4's OBS.13 split moves the paragraph, keep the test file-wide rather
  than pinning it to a specific sub-invariant.

Read first:

- The current `tests/specs/test_spec_hygiene.py` (it already has the monitor
  paragraph guard).
- `docs/specifications/07-System_Invariants.md` OBS.13.2 (or OBS.13 if
  Task 4 has not landed yet).
- The 2026-05-28 cleanup plan's Completion Criteria
  (`rg "Dealing with processes can be messy" docs/specifications/07-System_Invariants.md`).

Approach:

1. Confirm `tests/specs/test_spec_hygiene.py` contains a test equivalent to:

   ```python
   def test_obs13_monitor_justification_paragraph_present() -> None:
       """OBS.13 (or OBS.13.2 after the split) must explain why the
       TaskMonitor exists despite being operational evidence only.

       The phrase is the 2026-05-28 cleanup plan's acceptance criterion
       for the monitor justification paragraph; future drift would let
       agents lose the "why messy processes" anchor.
       """

       text = (SPEC_DIR / "07-System_Invariants.md").read_text(encoding="utf-8")
       assert "Dealing with processes can be messy" in text, (
           "OBS.13 must retain the 'messy processes' justification paragraph; "
           "see 2026-05-28 cleanup plan Task 12.4 and this plan's Task 4."
       )
   ```

2. The test is intentionally narrow (one substring assertion). It is not a
   prose snapshot. It does not require the paragraph to remain in OBS.13 vs
   OBS.13.2 — only that the anchor phrase exists somewhere in the file.
3. If Task 4 edits `07-System_Invariants.md`, run the test after the split and
   keep it passing without broadening it into a prose snapshot.

Red-green TDD:

- Already green in the current tree. To prove the guard works after Task 4,
  temporarily remove the paragraph, run the test, see it fail, restore the
  paragraph, see it pass. **Do not check in the removal step.**

Tests:

```bash
./.venv/bin/python -m pytest -q tests/specs/test_spec_hygiene.py
```

Do not:

- assert the full paragraph text (changing one word would fail CI
  unnecessarily),
- pin the test to a specific OBS.13 sub-invariant number (the file-wide
  substring check is more durable),
- add a similar guard for every other documentation invariant in this
  slice. The monitor paragraph is the highest-value drift target; other
  guards belong in a follow-up plan if they earn their keep.

Done when:

- The test remains present and passes against the current spec.
- A manual "remove paragraph, run test, restore paragraph" cycle has
  verified the guard fires when expected.

## Task 8: Pin the Monitor-Derived-Evidence Boundary

Outcome:
The documented invariant that Monitor store/collation output is derived
operational evidence is pinned without contradicting current code. Public
result/client paths must not treat Monitor store as result authority or bypass
command helpers. Public task status may use Monitor store as a derived fallback
after raw task-log retirement, but that exception must stay explicit,
best-effort, and command-layer owned.

Files to touch:

- `tests/architecture/test_import_boundaries.py` (extend the existing
  module rather than adding a new test file).
- `docs/specifications/07-System_Invariants.md` or
  `docs/specifications/09-Implementation_Plan.md` only if Task 4 reveals that
  the current specs do not state the derived-status fallback clearly enough.

Read first:

- The current `tests/architecture/test_import_boundaries.py` (understand
  the existing import-graph pattern it uses).
- `docs/specifications/07-System_Invariants.md` OBS.13 — specifically the
  rule that Monitor outputs are not lifecycle truth, status authority, or
  result authority.
- `docs/specifications/09-Implementation_Plan.md` [IP-1.0] Boundary
  Inventory (the row for `weft/core/monitor/`).
- `weft/commands/tasks.py` `_monitor_store_task_snapshot`, which is the current
  derived-status fallback and must not be treated as an accidental violation.

Approach:

1. Do **not** add a blanket import ban for Monitor store/collation from all
   public status code. Current `weft/commands/tasks.py` intentionally imports
   `open_monitor_store` to reconstruct a best-effort status snapshot after raw
   task-log evidence has been retired. That is derived evidence, not status
   authority.
2. Add a focused assertion that result/client paths do not directly import
   Monitor store or collation internals:

   - `weft/commands/result.py` does not import from those modules.
   - `weft/client/` modules (recursive) do not import from those modules
     (the existing client -> core import boundary may already cover this; keep
     the new assertion focused and readable if it adds signal).
3. Add an explicit allowlist/comment in the test for
   `weft.commands.tasks -> weft.core.monitor.store`, with the reason:
   "derived status fallback after raw task-log retirement; not result authority".
4. If the existing test file has a pattern like a tuple of
   `(consumer, set_of_disallowed)`, reuse it. If not, add the smallest helper
   that expresses the result/client rule and the task-status exception.
5. If Task 4 splits OBS.13, consider adding a sub-invariant that names this
   distinction directly:
   "Monitor store may support derived status reconstruction after raw log
   retirement, but it must not become result authority, control authority, or a
   replacement lifecycle truth source."

6. Use the same import-graph traversal style the existing test already uses;
   do not introduce a new dependency (e.g., a graph library). A simple
   `ast.parse` + walk for `Import` and `ImportFrom` nodes is sufficient
   given the file count.

Red-green TDD:

- Partially practical. The "red" state would be a deliberate one-line import
  from `weft.core.monitor.store` added to `weft/commands/result.py` or a
  `weft/client/` module for the purpose of seeing the test fail; do not check
  that in. Do not use `weft/commands/tasks.py` as the red-state target because
  that import is the documented exception.

Tests:

```bash
./.venv/bin/python -m pytest -q tests/architecture/test_import_boundaries.py
```

Do not:

- enforce a one-way rule for *all* of `weft/core/monitor/*` against *all*
  of `weft/commands/*` — task_monitor's own command surface (e.g.,
  `weft/commands/task_monitor.py`) legitimately calls into monitor internals,
  and `weft/commands/tasks.py` legitimately uses Monitor store as a derived
  status fallback,
- add similar guards for other documented invariants in the same slice;
  each architecture rule should land with its own focused test commit.

Done when:

- The new assertion is in `tests/architecture/test_import_boundaries.py`.
- The test documents the `weft/commands/tasks.py` exception instead of hiding
  it.
- The full architecture test suite is green.
- A manual cycle (add a deliberate disallowed import, see test fail,
  remove it, see test pass) has verified the guard works.

## Task 9: Curate the Plan Corpus

Outcome:
Every plan in `docs/plans/` falls into one of five classifications, and the
non-keep classifications are acted on. The plan README index is in sync.
The result is a smaller, intentional corpus that supports the existing
"plans are not a backlog" policy in practice.

Files to touch:

- `docs/plans/*.md` (selectively, per classification below).
- `docs/plans/README.md` (index updates, count refresh).
- `docs/specifications/*.md` only when a spec backlink to a removed plan
  must be updated.

Read first:

- `docs/plans/README.md` (Curation Policy and Status Taxonomy).
- `docs/agent-context/runbooks/writing-plans.md` (Backlink Rule).
- Every draft plan currently listed in `docs/plans/README.md` (22 of them
  today; an implementer should not curate plans they have not read).

Approach:

1. **Classification pass.** For each plan in `docs/plans/`, classify it as
   one of:

   - **KEEP-COMPLETED**: behavior is in shipped code; plan still describes
     the path correctly; metadata says `completed`. No action.
   - **MARK-COMPLETED**: behavior is in shipped code but plan metadata
     still says `draft`. Action: change metadata to `completed`, update
     README row.
   - **MARK-SUPERSEDED**: a later plan replaced this
     plan's approach; the metadata `Superseded by:` field is currently
     `none`. Action: update metadata to point at the successor plan using a
     valid relative Markdown link to an existing plan file; update README row.
     Do **not** put code/module pointers in `Superseded by:` — the plan
     metadata schema forbids that.
   - **RETIRE-EXPLORATORY**: plan is exploratory, audit-only, roadmap-only,
     process-only, or not implemented as written, and has no current
     explanatory value for shipped behavior. Action: `git rm` the file and
     remove its README row. **External review required before any retire
     action** — see Stop-and-re-evaluate gates.
   - **KEEP-DRAFT-ACTIVE**: plan is an active implementation slice or a
     meta-plan that has not landed yet; the author intends to continue
     work. Action: leave alone, but note its currency in this plan's
     implementation record.

2. **Classification evidence.** For each non-KEEP classification, the
   implementation record below must include one line of evidence:

   - For MARK-COMPLETED: "shipped at <commit>/<module:function>" or
     "behavior verified via <test file>".
   - For MARK-SUPERSEDED: "superseded by <plan file>" plus the evidence that
     the successor plan actually covers the older plan's scope.
   - For a plan whose old approach was replaced by shipped code but no
     successor plan exists: do not use MARK-SUPERSEDED. Either keep/mark it
     completed if it still explains current behavior, or classify it as
     RETIRE-EXPLORATORY if it has no current explanatory value and passes the
     retire bar. Put the code evidence in the implementation record, not in
     `Superseded by:`.
   - For RETIRE-EXPLORATORY: "exploratory only — never implemented; no
     spec backlinks; verified by `rg -n '<plan filename>' docs/ weft/`
     returning only this plan's own row".

3. **Action ordering.** Process classifications in this order to keep the
   corpus consistent at every commit boundary:

   1. **MARK-COMPLETED** edits first (status-only, low risk). Bundle into
      one commit per ~5 plans for review tractability.
   2. **MARK-SUPERSEDED** edits second (metadata-only, low risk). Bundle
      similarly.
   3. **README count and index** refresh after status changes settle.
   4. **RETIRE-EXPLORATORY** removals last, one plan per commit, each
      with the evidence line in the commit message. External review must
      have approved the retire list before any of these commits land.

4. **Suggested starting candidates for the implementer** (verify each
   against current code/spec before classifying):

   - MARK-SUPERSEDED candidates: drafts whose `Superseded by` field is
     currently `none` but whose work is clearly continued in a later plan
     (e.g., `2026-05-13-service-convergence-throttle-plan.md` is listed
     as superseded by `2026-05-15-manager-hot-loop-reduction-plan.md` —
     verify both are correctly labeled).
   - MARK-COMPLETED candidates: drafts ≥14 days old whose described
     behavior can be located in `weft/` via `rg` (e.g., 2026-05-06 and
     2026-05-07 drafts in the manager/service area — verify before
     changing).
   - RETIRE candidates: only plans with no spec backlinks, no incoming
     plan references, and no shipped code that traces back to them. These
     are the highest-risk action; treat with the most care.

5. **Bidirectional backlink check.** For each MARK-SUPERSEDED or RETIRE
   action, run `rg -n "<plan filename>" docs/specifications/` to find spec
   backlinks. Each hit must be updated to point at the successor plan or
   removed if the plan was retired. The 2026-05-28 cleanup plan's spec
   hygiene rule (`docs/plans/README.md` curation policy) requires this.

6. **README count refresh.** After all status changes and removals, update
   the line `There are currently NNN plan files in this directory.` in
   `docs/plans/README.md` to match `ls docs/plans/*.md | grep -v README | wc -l`.

Red-green TDD:

- `tests/specs/test_plan_metadata.py` is the existing guard. It must stay
  green at every commit boundary. The test catches:
  - README index entries without matching plan files,
  - plan files without matching README rows,
  - status mismatches between plan metadata and README rows.
- No new test is added in this task. Trying to write one for "the corpus
  is well-curated" would over-fit.

Tests:

```bash
./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py
```

Run after every commit in this task. If it fails, fix the metadata
inconsistency before continuing.

Stop and re-plan if:

- the RETIRE candidate list exceeds 25% of the corpus (too aggressive —
  scope the slice down),
- a draft plan cannot be classified from code/spec evidence (leave as
  KEEP-DRAFT-ACTIVE and note the ambiguity; do not guess),
- a spec backlink update would change the spec's normative content
  (stop — that is a spec change, not curation),
- external review of the RETIRE list has not yet returned approval (do
  not delete preemptively),
- a draft plan's `Goal` section describes behavior that contradicts a
  current spec invariant (the contradiction is a spec/code/plan
  disagreement worth surfacing in its own discussion, not silently
  retiring).

Do not:

- delete any plan without external review,
- mark a plan `completed` because it "sounds done" — require code/spec
  evidence,
- combine status changes and removals in the same commit,
- rewrite plan content during curation (curation is metadata + index +
  removal; not editorial revision),
- delete plans that document important architectural decisions even if
  the specific implementation moved (those are KEEP-COMPLETED, not RETIRE).

Done when:

- Every plan is classified.
- All MARK-COMPLETED and MARK-SUPERSEDED status changes have landed.
- All RETIRE actions approved by external review have landed (one commit
  each).
- The README count line matches the on-disk file count.
- `tests/specs/test_plan_metadata.py` is green.
- The implementation record below contains one evidence line per
  non-KEEP plan.

## Task 10: Refresh the Plan README Count and Cross-Links

Outcome:
The plan README's count line, index ordering, and cross-references to
related runbooks and specs are accurate and reflect the current corpus
state after Task 9.

Files to touch:

- `docs/plans/README.md`.

Read first:

- The README in full (it is short).
- `docs/agent-context/context.index.yaml` (only if Task 9 retired a plan
  that this index references; otherwise leave alone).

Approach:

1. Set the count line to match the current on-disk file count after Task 9:

   ```bash
   ls docs/plans/*.md | grep -v README | wc -l
   ```

2. Verify the index is sorted (the existing convention is reverse-chronological
   by filename date prefix; sanity-check with `ls docs/plans/*.md | sort -r`).
3. If `docs/agent-context/context.index.yaml` references any retired plan,
   remove that reference and bump the index's `last_updated` timestamp to
   today's date.

Red-green TDD:

- `tests/specs/test_plan_metadata.py` already enforces index/file consistency.

Tests:

```bash
./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py
```

Done when:

- The README count line matches reality.
- `tests/specs/test_plan_metadata.py` is green.
- Any retired-plan references in `context.index.yaml` are cleaned up.

## Task 11: Documentation Maintenance for Completed Slices

Outcome:
Every slice this plan completes leaves the surrounding spec mapping,
docstrings, README index, and `docs/lessons.md` synchronized with the
landed change.

Approach:

1. Before declaring this plan complete, run:

   ```bash
   git diff -- docs/specifications docs/plans tests weft
   ```

   For each file in the diff, confirm:

   - Spec mappings still name the right owner (especially for
     `07-System_Invariants.md` after Task 4's OBS.13 split, and any
     specs whose backlinks to retired plans were updated).
   - Module docstrings in `task_monitor.py` and `store.py` agree with the
     OBS.13.* identifiers introduced in Task 4.
   - The README plan count matches the on-disk file count.

2. If this plan exposed a repeated agent mistake (e.g., during plan
   curation, drift between completed-plan metadata and shipped behavior
   that was hard to detect), add a concise lesson to `docs/lessons.md`.
   Do not add lessons for one-off trivia.

3. If `docs/agent-context/context.index.yaml` needs a timestamp bump because
   any agent-context document changed, do that now.

Tests:

Final gates (the full set from the plan's Testing Plan below) before
declaring complete.

Done when:

- `git diff` shows only the intended documentation, comment, and test
  changes.
- Final gates pass.
- The implementation record below is complete.

## Testing Plan

Use the repo-managed toolchain. Do not assume global `pytest`, `ruff`, or
`mypy`.

Setup:

```bash
. ./.envrc
uv sync --all-extras
```

Per-slice verification (run after the relevant task):

- TOC additions (Task 1) and section-map comments (Tasks 2, 3):

  ```bash
  ./.venv/bin/ruff check weft/core/manager.py weft/core/monitor
  ./.venv/bin/mypy weft/core/manager.py weft/core/monitor
  ./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py
  ```

- OBS.13 split (Task 4):

  ```bash
  ./.venv/bin/python -m pytest -q tests/specs/test_spec_hygiene.py
  rg -n "\*\*OBS\.13\*\*" docs/specifications/07-System_Invariants.md
  rg -n "\*\*OBS\.13\.1\*\*" docs/specifications/07-System_Invariants.md
  rg "Dealing with processes can be messy" docs/specifications/07-System_Invariants.md
  ```

- store.py docstring (Task 5):

  ```bash
  rg -n "\[OBS\.13\].*operational evidence|operational evidence.*\[OBS\.13\]" weft/core/monitor/store.py
  ./.venv/bin/ruff check weft/core/monitor/store.py
  ./.venv/bin/python -m pytest -q tests/core/test_monitor_store.py
  ```

- Queue site comments (Task 6):

  ```bash
  rg -n "Queue\(" weft --glob "*.py"
  ./.venv/bin/ruff check weft/core/manager.py weft/core/spawn_requests.py weft/core/tasks/multiqueue_watcher.py weft/commands/interactive.py
  ./.venv/bin/mypy weft/core/manager.py weft/core/spawn_requests.py weft/core/tasks/multiqueue_watcher.py weft/commands/interactive.py
  ```

- Monitor paragraph guard (Task 7):

  ```bash
  ./.venv/bin/python -m pytest -q tests/specs/test_spec_hygiene.py
  ```

- Monitor-derived-evidence boundary test (Task 8):

  ```bash
  ./.venv/bin/python -m pytest -q tests/architecture/test_import_boundaries.py
  ```

- Plan curation (Tasks 9, 10):

  ```bash
  ./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py
  ```

Final gates before claiming the whole plan done:

```bash
. ./.envrc
./.venv/bin/python -m pytest -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft extensions/weft_docker extensions/weft_macos_sandbox tests
git diff --check
rg -n "\*\*OBS\.13\*\*" docs/specifications/07-System_Invariants.md
rg -n "\*\*OBS\.13\.1\*\*" docs/specifications/07-System_Invariants.md
rg "Dealing with processes can be messy" docs/specifications/07-System_Invariants.md
ls docs/plans/*.md | grep -v README | wc -l   # must equal README count line
```

What not to mock:

- Anything in Tasks 1–8. These are documentation, comment, and import-graph
  changes plus one derived-evidence boundary clarification; there is no
  broker/process behavior to mock.
- Anything in Task 9. Plan curation is a metadata operation; the relevant
  proof is `tests/specs/test_plan_metadata.py` against real on-disk files.

Mock only:

- Nothing in this plan. If a slice starts wanting mocks, that is a signal
  the slice is drifting into runtime behavior change — stop and re-plan.

## Verification and Gates

Before starting each task:

- Run `git status --short`. Do not revert unrelated working-tree changes.
- Identify the focused proof (failing test for Tasks 4, 7, 8; manual grep
  for Tasks 1, 2, 3, 5, 6; metadata test for Tasks 9, 10).
- Confirm external review status if the task requires it (Task 9 retire
  actions).

After each task:

- Run the task's per-slice verification commands.
- Inspect `git diff` for unintended changes.
- Confirm `tests/specs/test_plan_metadata.py` still passes (the corpus
  index can only be in one consistent state at a time).

Before marking the plan complete:

- All final gates above pass.
- The `**OBS.13**` umbrella marker and all introduced `**OBS.13.x**`
  sub-invariant markers resolve in `07-System_Invariants.md`.
- The monitor justification paragraph is still present.
- Every classified plan has its evidence line in this plan's implementation
  record.
- Fresh-eyes self-review is recorded.
- External review of the Task 9 retire list has been completed (or the
  plan stays at `draft` status until it is).

## Independent Review Loop

This plan is boundary-crossing for documentation (it changes the structure
of `[OBS.13]` and curates the plan corpus, which is a one-way door). It
should not reach `completed` status without external review.

Preferred reviewer:

- A different agent family than the author, if available.
- Otherwise, a separate review pass using a reviewer stance.

Reviewer should read:

- This plan.
- `AGENTS.md`.
- `docs/agent-context/runbooks/writing-plans.md`.
- `docs/agent-context/runbooks/hardening-plans.md`.
- `docs/specifications/07-System_Invariants.md` (OBS.13 in full, both before
  and after the proposed split).
- `docs/plans/README.md` (curation policy).
- The proposed RETIRE list from Task 9 (when the implementer has produced
  one).

Review prompt:

> Read the plan at
> `docs/plans/2026-05-28-documentation-clarity-and-plan-curation-plan.md`.
> Carefully examine the plan and the associated specs, code, and plan corpus.
> Look for errors, bad ideas, and latent ambiguities. In particular: does
> the OBS.13 split keep every existing `[OBS.13]` reference resolvable? Is
> the plan curation list defensible against the curation policy in
> `docs/plans/README.md`? Does Task 8 correctly distinguish result/client
> authority from the intentional `weft/commands/tasks.py` derived-status
> fallback? Are there documentation invariants this plan documents but does
> not guard, where a guard would be cheap and high-signal?
> Do not implement anything. Answer carefully: could a skilled engineer
> with zero Weft context implement this confidently and correctly? If not,
> list the exact blockers.

The plan author must respond to every external review finding by updating
the plan, explaining why the current path is still the best choice, or
recording why the point is out of scope.

## Fresh-Eyes Self-Review Log

The author must complete this section before implementation starts and
again after any material plan revision.

### Pass 1 checklist

- Are all tasks bite-sized and dependency-ordered?
- Does each task name files to touch and files to read first?
- Does each task say what not to change?
- Does each task name tests and anti-mocking guidance?
- Are runtime behavior changes out of scope unless separately planned?
- Is rollback possible for each task?
- Are spec/source references current?
- Is plan deletion treated as a one-way door?

### Pass 1 findings

- Confirmed the OBS.13 split is structured as additive (umbrella `[OBS.13]`
  stays resolvable, sub-invariants added underneath). Existing references must
  keep working.
- Task 6 is now an audit/preservation task because the current tree already
  annotates the direct SimpleBroker Queue edge sites surfaced by review. The
  `WeftContext.queue()` and `_queue()` helper implementation sites are
  explicitly excluded because they are the helper implementations, not
  violations.
- Task 9's classification list (KEEP-COMPLETED / MARK-COMPLETED /
  MARK-SUPERSEDED / RETIRE / KEEP-DRAFT-ACTIVE) and the action ordering
  (status changes before removals; one removal per commit; external review
  required for removals) treat plan deletion as a one-way door per
  `docs/agent-context/runbooks/hardening-plans.md` §12.
- Task 8 is intentionally narrow: result/client paths must not import Monitor
  store/collation directly, while `weft/commands/tasks.py` is an explicit
  derived-status fallback exception. A broad command-layer import ban would
  contradict current behavior.
- Each Task that adds a guard (4, 7, 8) follows red-green where practical
  and uses a focused substring/import assertion rather than a prose
  snapshot.

### Pass 2 checklist

- Re-read as a zero-context engineer with questionable taste.
- Search for vague verbs ("update the flow", "clean up", "improve",
  "adjust", "refactor") without a concrete file and boundary.
- Check whether any task could be misread as permission to split large
  files or move methods.
- Check whether the testing instructions allow broad mocks.
- Check whether the plan drifted into a runtime rewrite.
- Check whether the OBS.13 split could be misread as renaming or
  migrating identifiers.

### Pass 2 findings

- Task 2 and Task 3 explicitly forbid moving methods or changing
  signatures; the section-map comments are the only structural change.
- Task 4 explicitly forbids migrating existing `[OBS.13]` references in the
  same slice. The split is additive only.
- Task 9 explicitly forbids combining status changes with removals in
  the same commit; this preserves per-decision reversibility.
- No task allows broad mocks. The only test changes are
  `tests/specs/test_spec_hygiene.py` (additive substring assertion) and
  `tests/architecture/test_import_boundaries.py` (additive import-graph
  assertion).
- Residual risk: Task 9 is intentionally broad (touches the whole plan
  corpus). The slice-by-slice action ordering and per-commit external
  review for removals are the mitigations. The implementer should
  refuse to bundle removals into batch commits even under time pressure.

### Pass 3 checklist

- Have I addressed every documentation concern from the 2026-05-28
  review that the user asked to be addressed?
- Are there other "similar cleanups" implied by the user request that
  this plan should pick up?

### Pass 3 findings

- The user asked for: (1) mini-documentation/TOC inside Manager and
  Monitor, (2) OBS.13 made more usable, (3) plan review with delete/
  summarize/keep decisions, (4) other similar cleanups.
- Coverage: Task 1 (specs TOC), Tasks 2–3 (Manager / Monitor in-file
  navigation), Task 4 (OBS.13 split), Task 9 (plan curation). The
  "similar cleanups" basket includes Task 5 (store.py docstring fix from
  the 2026-05-28 review), Task 6 (Queue site comments missed by the
  cleanup plan's grep), Task 7 (monitor paragraph drift guard from the
  2026-05-28 review), Task 8 (monitor-derived-evidence boundary guard from
  the 2026-05-28 review).
- Deliberately out of scope: the `connect()` API redundancy (API
  design, not documentation), the cleanup plan's other grep-only
  hygiene items (the 2026-05-28 plan deliberately chose grep over tests
  for those; this plan does not second-guess that choice).
- Task 8 now treats `weft/commands/tasks.py` as an explicit derived-status
  fallback exception instead of an unresolved review question. The guard should
  cover result/client direct imports and document this exception.

### Pass 4 (final re-review before submitting)

- Final implementation matches the plan's no-runtime-behavior-change boundary:
  the intentional edits are spec/plan docs, source comments, import-boundary
  tests, plan metadata, and constants centralization required by the existing
  constants-policy gate.
- The curation pass did not delete plans. That is the right conservative
  choice without a separate external retire review. Four superseded drafts are
  retained with successor links; the rest of the old non-superseded drafts had
  current code/spec/test evidence and were marked completed.
- Risk left: TOC anchors are hand-authored GitHub-style anchors. No generator
  was introduced by design; the maintenance cost is manual review when headers
  move.

## Completion Criteria

This plan is complete when:

- TOCs are present on every spec over ~400 lines.
- `Manager`, `TaskMonitor`, and `MonitorStore` each have a top-of-class
  (or top-of-module) section-map comment referencing the relevant spec
  codes.
- `[OBS.13]` is restructured as an umbrella followed by 6–16 sub-invariants
  (`[OBS.13.1]` through `[OBS.13.N]`); pre-plan `[OBS.13]` references remain
  valid because the umbrella marker still exists.
- `weft/core/monitor/store.py` module docstring explicitly references
  `[OBS.13]` in the new sentence.
- Direct SimpleBroker `Queue(...)` sites outside helper implementations are
  audited and each accepted edge carries a nearby explanatory comment.
- `tests/specs/test_spec_hygiene.py` contains a narrow guard for the
  monitor justification paragraph and a guard for the OBS.13 sub-invariant
  decomposition.
- `tests/architecture/test_import_boundaries.py` contains a focused assertion
  that result/client paths do not import Monitor store/collation directly and
  documents the `weft/commands/tasks.py` derived-status fallback exception.
- Every plan in `docs/plans/` is classified; non-KEEP classifications
  have been acted on with one evidence line each in this plan's
  implementation record.
- `docs/plans/README.md` count and index are accurate.
- Final gates pass.
- External review findings are resolved or recorded as out of scope.
- A fresh-eyes Pass 4 review is recorded above.

## Implementation Record

### Task 1

- Added TOCs to `01-Core_Components.md`, `03-Manager_Architecture.md`,
  `05-Message_Flow_and_State.md`, and `07-System_Invariants.md`.
- Did not add a TOC to `09-Implementation_Plan.md`; it remains under the
  plan's ~400-line threshold.
- Verification: `tests/specs/test_plan_metadata.py` passed.

### Task 2

- Added a top-of-class `Manager` section map tied to existing `[MA-*]`
  mappings. No methods moved and no signatures changed.
- Verification: `ruff`, `mypy`, focused manager tests, and full pytest passed.

### Task 3

- Added section maps to `TaskMonitor` and `MonitorStore`; store docstring now
  references `[OBS.13.1]` alongside the umbrella `[OBS.13]`.
- Verification: `ruff`, `mypy`, focused monitor tests, and full pytest passed.

### Task 4

- Added the red test `test_obs13_is_decomposed_into_sub_invariants`; it failed
  before the spec edit because `**OBS.13.1**` did not exist.
- Split OBS.13 into umbrella `**OBS.13**` plus `**OBS.13.1**` through
  `**OBS.13.12**`. The exact phrase `Dealing with processes can be messy`
  remains in OBS.13.2.
- Verification: `tests/specs/test_spec_hygiene.py` passed; marker greps for
  `**OBS.13**`, `**OBS.13.1**`, and the monitor rationale passed.

### Task 5

- Preserved the `store.py` module docstring's `[OBS.13]` operational-evidence
  sentence and added `[OBS.13.1]` to its spec-reference list.
- Verification: the docstring grep, `ruff`, `mypy`, and monitor-store tests
  passed.

### Task 6

- Audited direct `Queue(...)` sites with `rg -n "Queue\\(" weft --glob "*.py"`.
- Excluded helper/runtime queue constructors: `WeftContext.queue()`,
  `BaseTask._queue()`, stdlib/thread queues, and multiprocessing queues.
- Accepted annotated SimpleBroker edge sites: CLI interactive queue-name
  adapter, pre-context spawn-request utilities, MultiQueueWatcher-owned queue
  handles, and manager short-lived probe/child-control handles.
- Verification: `ruff` and `mypy` passed for the audited files.

### Task 7

- Preserved the narrow monitor-rationale drift guard in
  `tests/specs/test_spec_hygiene.py`.
- Verification: `tests/specs/test_spec_hygiene.py` passed.

### Task 8

- Added `test_monitor_tables_are_not_result_or_client_authority` to
  `tests/architecture/test_import_boundaries.py`.
- The test documents the allowed `weft.commands.tasks ->
  weft.core.monitor.store` derived-status fallback and forbids direct
  Monitor store/collation/sql imports from result/client authority paths.
- Verification: `tests/architecture/test_import_boundaries.py` passed.

### Tasks 9-10

- Classified 129 plans. Final metadata state: 125 `completed`, 4 `draft`,
  zero retired. The remaining drafts are retained superseded drafts with
  successor links.
- MARK-COMPLETED evidence:
  - `2026-05-06-lifecycle-reconciliation-architecture-plan.md`: current
    [MF-5], [OBS.11], [OBS.13], [OBS.14], and [OBS.17] plus
    `weft/core/task_evidence.py` and `weft/core/monitor/task_monitor.py`.
  - `2026-05-06-status-coherence-and-stale-pid-liveness-plan.md`:
    `tests/commands/test_status.py` and [OBS.11]/[OBS.11a].
  - `2026-05-06-task-evidence-reconciliation-model-plan.md`:
    `weft/core/task_evidence.py` and `tests/commands/test_task_evidence.py`.
  - `2026-05-06-terminal-publication-hardening-plan.md`: typed terminal
    `ctrl_out` handling in [MF-3]/[MF-5] and task command/evidence tests.
  - `2026-05-07-extended-ping-pong-state-probe-plan.md`: structured
    keyed PING/PONG behavior in `weft/core/tasks/base.py`,
    `weft/core/control_probe.py`, and task evidence/control tests.
  - `2026-05-07-phase-7-task-monitor-supervision-and-cleanup-plan.md`:
    manager-supervised `TaskMonitor` in [MA-1.6a]/[MF-5] and task-monitor
    tests.
  - `2026-05-08-agent-session-and-task-startup-observability-plan.md`:
    `weft/core/runner_diagnostics.py`, `AgentSession.wait_ready`, and
    runner/status diagnostic tests.
  - `2026-05-09-service-liveness-and-health-convergence-plan.md`:
    `weft/core/manager_services.py`, `weft/commands/system.py`, and
    manager-service tests.
  - `2026-05-11-canonical-service-reducer-fix-plan.md`:
    `reduce_managed_service_state` and `tests/core/test_manager_services.py`.
  - `2026-05-11-internal-service-observability-plan.md`:
    `weft/commands/system.py::_collect_internal_service_snapshots` and
    [MA-1.6a].
  - `2026-05-11-manager-serve-operational-log-plan.md`:
    `weft/core/serve_log.py`, manager serve-log integration, and constants
    tests.
  - `2026-05-11-manager-work-stealing-dispatch-plan.md`:
    [MF-6], [MA-1.1], manager atomic reservation paths, and manager tests.
  - `2026-05-11-service-convergence-and-manager-registry-bounding-plan.md`:
    [MA-1.6a], manager convergence throttling, and service registry tests.
  - `2026-05-13-internal-state-machine-helper-plan.md`:
    `weft/core/state_machines.py` and `tests/core/test_state_machines.py`.
  - `2026-05-15-swappable-task-log-family-scanner-plan.md`:
    current [MF-5] table-driven scan-limit contract and task-monitor tests.
  - `2026-05-18-monitor-table-driven-retained-log-cleanup-plan.md`:
    `weft/core/monitor/store.py`, `sql.py`, `task_monitor.py`, and monitor
    store/task-monitor tests.
  - `2026-05-27-service-collation-reporting-plan.md`: service collation
    fields in store/task-monitor/external-log code and tests.
  - `2026-05-28-documentation-clarity-and-plan-curation-plan.md`: this
    implementation record and final gates.
- MARK-SUPERSEDED retained:
  - `2026-05-08-manager-owned-internal-service-supervision-plan.md` ->
    `2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md`;
    metadata was normalized to a Markdown link.
  - `2026-05-08-phase-7-manager-service-reconciler-cleanup-plan.md` ->
    `2026-05-08-deterministic-manager-service-reconciler-plan.md`.
  - `2026-05-13-service-convergence-throttle-plan.md` ->
    `2026-05-15-manager-hot-loop-reduction-plan.md`.
  - `2026-05-15-manager-hot-loop-reduction-plan.md` ->
    `2026-05-15-task-reactor-and-evidence-worker-plan.md`.
- RETIRE removals: none. No external retire review was requested, and no plan
  deletion was necessary for this slice.
- Verification: `tests/specs/test_plan_metadata.py` passed; README count
  matches `129`.

### Constants Gate Fix

- Full pytest exposed dirty-tree constants-policy violations in
  `weft/core/monitor/policies/runtime_control.py` and
  `extensions/weft_docker/weft_docker/profiles.py`.
- Moved those production constants into `weft/_constants.py` and imported them
  from the runtime modules. This was required to make the existing
  `tests/system/test_constants.py` gate pass.

### Final Gates

- `./.venv/bin/python -m pytest -q`: passed; 2 skipped
  (Postgres env-only init, Windows path handling).
- `./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox`:
  passed for 176 source files.
- `./.venv/bin/ruff check weft extensions/weft_docker extensions/weft_macos_sandbox tests`:
  passed.
- `git diff --check`: passed.
- OBS marker/rationale greps passed for `**OBS.13**`, `**OBS.13.1**`, and
  `Dealing with processes can be messy`.
- `ls docs/plans/*.md | grep -v '/README.md$' | wc -l`: `129`, matching the
  README count line.

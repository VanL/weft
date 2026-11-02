# Crystallize Program Theory

Status: Active — adopted from agent-theory @ `8f41a9d`, updated to
`296a7cd` via `docs/plans/2026-10-14-agent-theory-delta-wave-plan.md`, and
updated to `e6ac579` via
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md`.

## Purpose

Interview the product owner relentlessly until a theory file can be written:

- **Product theory** — `docs/program-theory.md` (apex shared language), or
- **Module theory** — a human-decided **extension** of that model for one
  weakly coupled module that has theory-level invariants applying only to that
  module (typical path: `<module>/MODULE-THEORY.md`).

Module theory is not a lesser document, and it is not a routine split for every
subsystem: use it only when the human owner decides a module is weakly coupled
to the rest of the program and carries module-only theory-level invariants. See
`docs/program-theory.md` `[WT-0]` (and the
`agent-theory` hub's `[AT-THEORY-2.1]` and `[AT-THEORY-6]`).

The result is a transfer surface for agents and humans—not a requirements dump
and not a copy of the agent-theory hub's own theory.

Inspired by interview-style “grill” workflows: one decision at a time, owner
owns the answers, environment facts are looked up rather than asked.

## When To Use

- A consumer repo still has the bootstrap **program-theory stub** (product).
- Product identity is only in chat (“X-ness”) or a long README residual.
- Agents repeatedly propose category errors or re-litigate non-goals.
- **Extending the model (human decision):** a module is weakly coupled to the
  rest of the program and has theory-level invariants, ownership boundaries, or
  non-goals that apply only to that module—write **module theory** instead of
  bulking the root file.
- Before class-5 identity work without a written conceptual account.
- During an authorized coalescing sweep when cold lessons or retired plans carry
  durable architectural rationale that belongs in the program theory rather than
  a process rule.
- Do NOT use mid-task without authorization when the edit would change normative
  product scope or reverse a standing refused alternative (`[ALT-WT-*]`): surface
  `RECONSIDERATION REQUESTED` for human decision first.

## When Not To Use

- Editing the **`agent-theory` hub's** meta-theory of agent-theory itself (edit
  the hub's `docs/program-theory.md` under normal review rather than running a
  product-owner interview).
- Pure process changes with no product-identity impact.
- Writing exact behavioral contracts (use specs).

## Governing References

- `docs/program-theory.md` (this repository's canonical program theory)
- `docs/agent-context/decision-hierarchy.md` (authority order, `[DOM-15]`
  classification, and preflight check)
- `docs/agent-context/runbooks/writing-plans.md` (§4b–4d for spec-changing work)
- `docs/specifications/README.md` (specification corpus structure and traceability)
- Hub meta-theory (in the `agent-theory` hub repository): `[AT-THEORY-0]`–`[AT-THEORY-8]`

## Read First

1. Existing `docs/program-theory.md` (hub account, stub, or draft).
2. Product entry (README, agent kernel if any, product-context if any).
3. Any module path the user named for subsystem theory.
4. Recent plans or lessons that show identity friction.

## Blast Radius

- Creates or replaces `docs/program-theory.md` (product) or a module
  `MODULE-THEORY.md` / agreed path.
- May require AGENTS.md / `context.index.yaml` read-order updates so theory
  loads at the right scope.
- Does **not** silently change product specs; identity changes may imply
  later class-5 contract work.
- `docs/program-theory.md` (`[WT-*]` and `[ALT-WT-*]` sections)
- module-level docstrings or local `README.md` theory notes in `weft/` when a
  subsystem boundary needs progressive-disclosure context
- `docs/specifications/` when crystallizing the theory reveals an undocumented
  product contract that requires a spec delta

## Workflow

### 1. Frame

State that:

- Program theory is Naur-style working understanding externalized as a
  **current account**.
- Possession stays practical; the file is a transfer surface.
- Hub agent-theory text must not stand as product identity.
- **Module theory is how we extend the product model** when depth is local
  (`[WT-0]` / hub `[AT-THEORY-2.1]`)—not optional color commentary.
- You will ask **one question at a time**, give a **recommended answer**
  when useful, and wait for the owner.

Decide **scope up front** with the owner: product apex, module extension, or
both (thin product + module). Do not draft the full document until shared
understanding on the spine—or they explicitly ask for a draft from answers so far.

### 2. Interview (one question at a time)

This skill owns the interview spine (referenced by the `agent-theory` hub's
`[AT-THEORY-8]`). For each item:

1. Ask the question.
2. Offer a short recommended direction grounded in repo evidence when possible.
3. Wait for the owner's decision.
4. Record the answer; note open dependencies.
5. Only then proceed to the next question.

If a **fact** is in the repo, look it up. **Decisions** stay with the owner.

Walk branches: if non-goals depend on topology ownership, resolve topology
before finalizing non-goals.

#### Spine questions

1. **Problem world** — What affairs of the world does this program handle?
   For whom? What fails if it does not exist?
2. **Desired feel & compositional idiom** — What should using and extending it
   feel like? What are the core composable operations new work must build on
   rather than inventing side doors?
3. **Whole-program metaphor** — In three to seven bullets, what is the
   mental model (not the file tree)?
4. **Core concepts** — Name each concept, its meaning, and its **owner**
   (this product / a module / the user / another system).
5. **Matching surface & taste anchors** — How do CLI, API, UI, or docs express
   the same model without inventing a second product? What is the command/API
   grammar (e.g., top-level reflex verbs vs. `noun verb`, projected domain
   nouns, concrete taste-anchor examples)?
6. **Negative knowledge (two parts)** —
   - *What the system is not:* What will competent people propose that you will
     refuse on boundary or identity grounds, and why?
   - *Tried and discarded paths:* What plausible designs or fixes were already
     tried (or investigated) and discarded, and why?
   - For both, what observable condition would make you reconsider?
7. **Generative rule** — Is there one question that organizes a large part
   of the design (as control locality might organize a taxonomy)?
8. **Identity (theory-level) invariants & delivery / safety honesty** — Which
   invariants and architectural choices are core to the identity of the product
   and shape the entire program—and why do the boundaries sit where they
   do—such that if they changed, the program would start to become something
   else, and if the internal implementation were replaced tomorrow, the new
   implementation would still be measured against them? Where can work be lost,
   duplicated, or weakly authenticated? Name the guarantee narrowly.
9. **Tensions** — What live tensions would falsify the account if they
   worsened?
10. **Extension by module theory (human decision)** — Is any module weakly
    coupled enough to the rest of the program, with theory-level invariants
    that apply only to that module, to warrant its own `MODULE-THEORY.md`? Which
    path owns that file?
11. **Possession test** — What surprises (bugs or bad agent paths) would
    force a theory revision rather than a local patch?
12. **Replacement check** — If this were only a README restatement, what
    judgment would still be missing?

Stop when the owner can **place a feature**, **refuse a category error**,
and **predict a failure mode** without re-deriving the product from scratch.

### 3. Choose extension shape

| Situation | Action |
|-----------|--------|
| No product theory yet | Crystallize **product** `docs/program-theory.md` first |
| Product theory exists; a weakly coupled module has module-only theory-level invariants and the human owner wants a module theory | **Extend** with module theory; do not bulk product theory |
| Answers mix product-wide theory and a weakly coupled module's own theory | Split (with human owner agreement): thin product apex + module file(s) |
| Content is routine subsystem depth or exact rules only | Stop; write or update a **contract** (`docs/specifications/`), not theory |

Extending a project theory with a module theory is ultimately the human's
decision (`[WT-7]`). Module theories must not be used to justify
variances from the program theory when one theory should apply to the whole
program; use a module theory only when a specific module is weakly coupled to
the rest of the program and has theory-level invariants that apply only to that
module. When a module theory is genuinely warranted, require: ownership table,
generative model or equivalent, local non-goals, required action on entry, “no
sibling module theory required,” and promotion path for any product-scope ALT
parked there.

### 4. Draft

**Product** — write `docs/program-theory.md` with at least:

- Status / Owner / Boundary / Verification / Required action
- What program theory means (short; link Naur)
- Purpose and desired feel; whole-program mental model
- Core concepts, ownership, and surface idiom / taste anchors (command or API
  grammar + composable operations to build on — *taste up front*, while leaving
  internal storage schemas to vertical implementation slices; `[REV-AT-005]`)
- Identity (theory-level) invariants — the choices that shape the entire program
  (general enough that changing implementations remain measurable against them,
  specific enough to beat aphorisms; `[REV-WT-004]`)
- Principles; two-part negative knowledge (what the system is not, and paths
  tried and discarded); falsifiers
- How the model is extended (pointer to module theory convention)
- Revisions/ALT if decided; related contracts

Choose references on two tests: **accurately represented** and
**load-bearing**. A load-bearing reference contributes a concept,
distinction, refusal, or vocabulary the theory actually uses. Make
that contribution explicit where it would otherwise be unclear; omit
ornamental citations. Treat any register effect as a drafting
hypothesis, never as evidence that the theory or its outputs are
correct ([REV-AT-003]).

**Module** — write `<module>/MODULE-THEORY.md` (or agreed path) with at least:

- Status / Owner / Boundary / Verification / Required action (“read on entry
  to … before changing …”)
- Specializes: product program theory (link)
- What this document is / is not (contracts vs theory)
- Ownership table (owner / consumer / propagator)
- What the module owns; what it is not
- Generative model or design consequences
- Tensions and falsifiers; REV/ALT records
- Related contracts; promotion notes for product-scope items

Use stable section codes if the repo already uses them (`[THEORY-n]`,
`[MODULE-n]`, etc.).

### 5. Wire entry (if missing)

- **Product:** theory early in `context.index.yaml` `read_order` for
  product-scope repository work.
- **Module:** document entry in product theory and/or AGENTS (“when touching
  `path/…`, read `…/MODULE-THEORY.md`”). **Do not** put every module theory
  on global session start.

Do not force full process read order onto product-use-only paths.

### 6. On refuse or revise — write a decision record

If the interview **refuses or defers** a competent proposal, or **changes** the
working model, append a filled ALT or REV using the field templates in this
skill. Do this **when it happens**, not as a quota of empty records.

**Apply the four-part admission test to refused alternatives (`[ALT-WT-*]`).**
Record a rejected or refused alternative when at least one holds:

- **Likely recurrence:** future sessions or reviewers are likely to propose it
  again as an "obvious" simplification or feature.
- **Material investigation cost:** re-evaluating why it fails costs significant
  time or experiment budget.
- **Hidden constraint exposed:** the refusal rests on a non-obvious invariant
  (for example, spawn-only multiprocess safety, queue-truth vs auxiliary cache,
  or SQLite/Windows handle lifetime).
- **Harm from blind retry:** trying the alternative silently corrupts state,
  weakens an invariant, or pulls Weft above the substrate layer.

For each admitted `[ALT-WT-*]` entry, state what was refused, the concrete
mechanism that makes it wrong here, and what condition (if any) would justify
reopening it.

Do not create empty `[ALT-001]` placeholders or decision-case directories.

### 7. Confirm

Ask the owner to test possession:

- Place one hypothetical feature.
- Refuse one category error.
- Name one surprise that would revise the theory.

For a **product apex**, also walk the fuller owner's checklist — the nine
understandings the founding dialogue named (origin: the hub's lore record
of the 2026-07-29 backstitch README dialogue). The owner should be able to
state, without reading the draft back:

1. what concepts exist;
2. what each concept means;
3. which component owns each behavior;
4. why the boundaries are where they are;
5. which alternatives were rejected and why;
6. what must remain invariant;
7. what the system explicitly does not do;
8. how a proposed change propagates through the rest of the model;
9. what evidence would show the current understanding is wrong.

Any item the owner cannot answer marks a section that is not yet theory:
interview further there, or keep the account's Status at `Draft` and record
the gap. Verify that Q6 (refusals and discarded paths) and Q8 (identity /
theory-level invariants—the choices that shape the entire program across
re-implementation) produced concrete answers for items 4, 5, and 6 rather than
aphorisms or implementation-only mechanics; if either is thin, probe it
explicitly before marking the account `Active`.

When crystallizing or revising theory for a repository that already has
shipping code or specs—and again at each release or Class-5 completion alongside
the `[WT-7]` possession check—the product owner (or authoring agent for
owner confirmation) produces or refreshes a **theory-versus-code (and
theory-versus-spec) audit table** in the plan or `docs/lessons.md`: map each
theory claim and identity invariant against current specs and code (carrying
forward unchanged rows by reference on subsequent refreshes), marking each row
as aligned, drifted (to be morphed via a plan), or evidence that the theory
itself needs revision (`[WT-0]`).

Revise the draft until they confirm.

### 8. Verify identifiers and run gates

Check that every cited file path, symbol, spec code, and `[WT-*]` /
`[ALT-WT-*]` identifier resolves, and run `bin/check-doc-paths` and
`tests/specs/test_spec_hygiene.py`.

## Decision record templates

### ALT (rejected / deferred / …)

```markdown
### [ALT-<SCOPE>-<NNN>] Short title

Disposition: rejected | deferred | adopted | superseded | invalidated
Owner: <decision owner>
Governs: <theory section, module theory, or contract reference>
Source record: none | <plan path> | <commit or issue>
Candidate: <what was proposed>
Why plausible: <steelman>
Evidence:
- contemporaneous | owner-recalled | inferred | unknown: <source>
Reason: <why this disposition>
Current consequence: <what work must do now>
Reconsider when: <observable condition — not vibes>
Promoted to: none | <id if moved upstairs or into a contract>
```

### REV (theory change)

```markdown
### [REV-<SCOPE>-<NNN>] Short title

Current account: <revised theory — put current first>
Supersedes: <short prior account; do not let it compete with current>
Pressure: <what made the prior account inadequate>
Evidence:
- contemporaneous | owner-recalled | inferred | unknown: <source>
```

## Output Standard

- Written theory file at the agreed path.
- Explicit non-goals and ownership table.
- Entry-order note or patch so the file is loadable by agents.
- Any **real** ALT/REV from the interview filled in full; zero empty slots.
- List of follow-ups (spec promotions, parked ALTs) without implementing them
  unless asked.
- `docs/program-theory.md` updated with concise, code- and spec-grounded `[WT-*]`
  or `[ALT-WT-*]` entries
- Any new or revised refused alternative states its mechanism and reopening
  condition
- All backticked repository paths and spec references pass `bin/check-doc-paths`
  and `tests/specs/`

## Anti-patterns

- Copying the hub's agent-theory program-theory into a product repo as-is.
- Stuffing module depth into product theory “so agents see it,” instead of
  **extending** with module theory and entry wiring.
- Writing module theory that requires three sibling theories to place work
  (broken ownership table).
- Turning the interview into a full architecture dump or API catalog.
- Asking multiple independent questions in one turn.
- Acting on implementation before shared understanding is confirmed.
- Treating the written file as proof of possession without the placement/refusal
  test.
- Pre-creating empty ALT/REV records or decision directories “for later.”

## Module-theory drafting checklist

When extending via module theory, require:

1. File next to owning code (conventional `MODULE-THEORY.md`) or an agreed path
   documented from product theory / AGENTS.
2. Metadata: Status, Owner, Boundary, Verification, Required action
   (“read on entry to … before changing …”).
3. Ownership table (owner / consumer / propagator). Two modules claiming
   owner of the same concept is a gate failure.
4. Generative model or design consequences; local non-goals.
5. Point exact rules at contracts/fixtures; do not duplicate them as theory.
6. Local ALT/REV when real; park product-scope ALTs for promotion.
7. Wire entry: agents working in that tree read module theory **after** product
   theory (or overview) and **before** changing the module — not on every
   global session start.
8. Actionable without reading sibling modules.

## Maintenance Notes

- This skill is the sole home for the interview spine, ALT/REV field templates,
  module drafting checklist, and entry-wiring procedure ([AT-THEORY-8] by
  reference). Do not re-expand those into hub `docs/program-theory.md`.
- If interviews always need the same extra domain questions, promote them into
  this skill.
- If consumers keep shipping without replacing the stub, strengthen bootstrap
  messaging and AGENTS required-action language.

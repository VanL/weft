# Spec Corpus Current-vs-Planned Split Plan

## 1. Goal

Make `docs/specifications/` truthful at a glance.

After this change, the canonical numbered specs must describe current Weft
behavior and the reasons behind that behavior. Intended but not implemented
material must move into separate companion docs with adjacent numbering so the
relationship stays obvious without mixing authority tiers in one file.

Definition of done:

- Canonical spec files describe current contract, current implementation
  boundaries, and rationale.
- Intended but not implemented behavior lives in adjacent companion docs named
  `NN[A]-...`.
- Removed surfaces are documented as removed or superseded, not as "not yet
  implemented."
- `docs/specifications/README.md` explains the split explicitly.
- The touched code/doc boundaries keep bidirectional traceability where current
  behavior is implemented.

## 2. Source Documents

Read these before editing:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/agent-context/README.md`](../agent-context/README.md)
3. [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
4. [`docs/agent-context/principles.md`](../agent-context/principles.md)
5. [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
6. [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
7. [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
8. [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
9. [`problem-report-spec-corpus-drift.md`](../../problem-report-spec-corpus-drift.md)
10. [`docs/specifications/README.md`](../specifications/README.md)
11. [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
12. [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
13. [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md)
14. [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)
15. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
16. [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
17. [`docs/specifications/09-Implementation_Plan.md`](../specifications/09-Implementation_Plan.md)
18. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
19. [`docs/specifications/11-CLI_Architecture_Crosswalk.md`](../specifications/11-CLI_Architecture_Crosswalk.md)
20. [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)

## 3. Split Rules

### 3.1 Canonical spec rules

Canonical specs may contain:

- current public contract
- current implementation mapping
- current limitations when they affect real operator or developer behavior
- rationale for why the current design is shaped the way it is

Canonical specs must not contain:

- historical design archaeology
- pseudocode for nonexistent classes or modules
- removed surfaces framed as pending features
- future API semantics stated in the same voice as current behavior

### 3.2 Companion doc rules

Adjacent `A` docs hold intended-but-unimplemented material that is still worth
tracking next to the owning spec. These docs may include:

- desired future semantics
- pseudocode for possible APIs or components
- testing gaps and future suites
- planned CLI or runtime follow-ups

They must not pretend to be current contract.

### 3.3 Removed-surface rule

If a surface was intentionally removed or superseded, the canonical doc should
say so directly and give the current replacement.

Example:

- global `--dir` / `--file` flags were removed as Weft moved to backend-neutral
  broker targeting; current commands use per-command `--context` and SimpleBroker
  target resolution instead

## 4. Planned File Layout

Companion docs to create in this slice:

- [`docs/specifications/00A-Overview_and_Architecture_Planned.md`](../specifications/00A-Overview_and_Architecture_Planned.md)
- [`docs/specifications/01A-Core_Components_Planned.md`](../specifications/01A-Core_Components_Planned.md)
- [`docs/specifications/04A-SimpleBroker_Integration_Planned.md`](../specifications/04A-SimpleBroker_Integration_Planned.md)
- [`docs/specifications/06A-Resource_Management_Planned.md`](../specifications/06A-Resource_Management_Planned.md)
- [`docs/specifications/07A-System_Invariants_Planned.md`](../specifications/07A-System_Invariants_Planned.md)
- [`docs/specifications/08A-Testing_Strategy_Planned.md`](../specifications/08A-Testing_Strategy_Planned.md)
- [`docs/specifications/09A-Implementation_Roadmap_Planned.md`](../specifications/09A-Implementation_Roadmap_Planned.md)
- [`docs/specifications/10A-CLI_Interface_Planned.md`](../specifications/10A-CLI_Interface_Planned.md)
- [`docs/specifications/11A-CLI_Architecture_Crosswalk_Planned.md`](../specifications/11A-CLI_Architecture_Crosswalk_Planned.md)
- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)

If a file only needs a short pointer, keep the companion doc minimal rather than
recreating an entire parallel spec.

## 5. Work Plan

### 5.1 Authority split

1. Update `docs/specifications/README.md` to define the authority tiers.
2. Add backlinks from each touched canonical spec to its companion `A` doc.
3. Ensure the companion docs state that they are planned material, not current
   contract.

### 5.2 Canonical cleanup

1. Rewrite `00`, `01`, `04`, `10`, and `11` so they stop speaking in terms of
   nonexistent current components.
2. Rewrite `06`, `07`, `08`, and `13` so design-reference pseudocode moves out
   of the canonical tier.
3. Recast `09` into an honest current implementation status summary and move the
   roadmap material into `09A`.

### 5.3 Traceability cleanup

1. Fix stale code/spec references in touched implementation files.
2. Add missing spec references in high-signal boundary modules touched by the
   canonical docs, especially CLI and manager/context surfaces.

## 6. Verification

- Run targeted searches for `NOT YET IMPLEMENTED`, removed CLI flags, and
  nonexistent module names in canonical specs.
- Verify the new companion docs are linked from both `README.md` and the owning
  canonical docs.
- Run a focused traceability check for touched code files and spec section codes.
- Run an independent review pass over the changed spec corpus.

## 7. Risks

The main failure mode is under-correcting the corpus by moving only the obvious
examples while leaving softer design-reference pseudocode in canonical files.
The second failure mode is over-correcting by stripping the "why" and leaving
only a dry reference manual. The fix must preserve rationale while removing
authority ambiguity.

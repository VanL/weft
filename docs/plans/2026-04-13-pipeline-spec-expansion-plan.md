# Pipeline Spec Expansion Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## Goal

Define a prescriptive pipeline product contract for Weft that matches the
project's intended UX: simple verbs, layered composition, Unix-friendly
surfaces, and strong observability. This plan is for specification work only.
It does not implement the runtime changes; it makes the target behavior,
non-goals, and implementation order explicit so later code work has a stable
source of truth. The evolved target shape is a first-class pipeline task with
generated edge tasks, pre-started waiting stage/edge children for ordinary
linear runs, fail-fast semantics, and pipeline-owned live status snapshots over
durable audit logs.

## Source Documents

Source specs:
- `docs/specifications/00-Overview_and_Architecture.md`
- `docs/specifications/02-TaskSpec.md [TS-1.2]`
- `docs/specifications/05-Message_Flow_and_State.md [MF-4], [MF-6]`
- `docs/specifications/10-CLI_Interface.md [CLI-0], [CLI-1.1.1]`

Guidance:
- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Product context:
- `README.md`
- `../simplebroker/README.md`

External references that inform the product stance:
- Absurd durable execution docs and repository
- Kamaelia / Axon docs and the Europython "Pragmatic Concurrency" tutorial

## Context and Key Files

Files to modify:
- `docs/specifications/12-Pipeline_Composition_and_UX.md` (new)
- `docs/specifications/00-Overview_and_Architecture.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`

Read first:
- `README.md`
- `../simplebroker/README.md`
- `docs/specifications/00-Overview_and_Architecture.md`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`

Current structure:
- `weft run --pipeline` exists today as a sequential CLI-owned runner in
  `weft/commands/run.py`, not as a first-class durable pipeline runtime.
- Stored pipeline specs already live under `.weft/pipelines/`.
- Autostart manifests can name pipeline targets in docs, but the manager does
  not yet launch them.
- The public product story already mentions pipelines, but there is no single
  spec that says what a pipeline is, what it is not, or how the UX should stay
  aligned with Weft and SimpleBroker.

Comprehension questions before editing:
1. Which command is the center of gravity for Weft's UX today?
   Answer: `weft run`.
2. Where does current pipeline execution happen today?
   Answer: in the CLI path, not in a first-class orchestrator runtime.
3. Which queue/log surface remains the canonical observability path?
   Answer: task-local queues plus `weft.log.tasks`.

## Invariants and Constraints

- Keep `weft run` as the center of gravity. Do not introduce a separate top-level
  workflow command family as the primary UX.
- Pipelines must be specified as composition over tasks and queue-visible
  behavior, not as a hidden second product with its own opaque control plane.
- The spec must stay honest about implementation status. Target behavior and
  current behavior must be separated clearly.
- Preserve the repo's "everything is a task" model. If pipelines become
  first-class at runtime, they should still be task-shaped objects.
- Keep observability explicit: child stage identity, queue-visible behavior, and
  task-log visibility must remain central to the product contract.
- Do not promise arbitrary DAGs, distributed orchestration, or code-first SDK
  workflow authoring as part of the initial pipeline contract.
- Do not create spec drift between overview, CLI, TaskSpec, and message-flow
  documents.
- No drive-by refactor of unrelated specs.

## Tasks

1. Add a dedicated pipeline specification as the source of truth.
   - Outcome: one document defines the pipeline UX, scope, non-goals,
     observability model, runtime shape, and phased implementation order.
   - Files to touch:
     - `docs/specifications/12-Pipeline_Composition_and_UX.md`
   - Read first:
     - `README.md`
     - `../simplebroker/README.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
   - Reuse:
     - existing spec style with section reference codes and implementation
       snapshots
   - Stop and re-evaluate if:
     - the document starts describing a generic workflow engine instead of a
       Weft-shaped composition surface
     - the document assumes a second durable path outside the canonical task
       spine
   - Done signal:
     - the new spec can answer "what is a pipeline?" and "what does it not do?"
       without relying on any other file

2. Align the overview and CLI specs to the new source of truth.
   - Outcome: overview and CLI docs describe pipelines as task-shaped
     composition, keep the simple-verb stance explicit, and name the current
     phase-0 limitations honestly.
   - Files to touch:
     - `docs/specifications/00-Overview_and_Architecture.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Read first:
     - `docs/specifications/12-Pipeline_Composition_and_UX.md`
   - Reuse:
     - existing "Implementation snapshot" style for current behavior notes
   - Stop and re-evaluate if:
     - the CLI spec starts promising behavior that depends on code not yet
       implemented without marking the phase boundary
   - Done signal:
     - overview and CLI docs both point to the new pipeline spec and no longer
       imply that a standalone `weft pipeline` verb family is the intended
       center of gravity

3. Align TaskSpec and message-flow docs around pipeline scope and runtime order.
   - Outcome: pipeline targets, autostart, and message-flow docs describe the
     same phased model and do not blur current sequential behavior with the
     first-class target runtime.
   - Files to touch:
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
   - Read first:
     - `docs/specifications/12-Pipeline_Composition_and_UX.md`
   - Reuse:
     - current autostart and spawn-flow sections; tighten them instead of
       inventing parallel terminology
   - Stop and re-evaluate if:
     - the docs start implying that stages are anything other than ordinary
       tasks in the early phases
   - Done signal:
     - TaskSpec and message-flow language matches the new pipeline source of
       truth and explicitly names the unsupported pieces

4. Run an independent review pass on the doc set and tighten ambiguities.
   - Outcome: a second model family can read the spec set and say whether the
     contract is implementable without guesswork.
   - Files to review:
     - `docs/specifications/12-Pipeline_Composition_and_UX.md`
     - `docs/specifications/00-Overview_and_Architecture.md`
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Review prompt:
     - "Read the pipeline spec set and look for ambiguities, hidden conflicts,
       or product-shape drift. Could you implement this confidently without
       accidentally turning Weft into a workflow platform?"
   - Done signal:
     - every accepted ambiguity is fixed in the docs or explicitly recorded as a
       deliberate tradeoff

## Verification

- Read the new pipeline spec front to back and verify it states:
  - what pipelines are
  - what pipelines are not
  - which verbs apply
  - what the phase order is
- Check backlinks and related-doc links across the touched spec files.
- Run:
  - `rg -n "Pipeline Composition and UX|pipeline-spec-expansion-plan" docs/specifications docs/plans`

## Rollback

This is doc-only work. Rollback is a revert of the touched spec files and the
plan file. No persisted data or code path changes are involved.

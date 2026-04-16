# First-Class Linear Pipeline Runtime Implementation Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

This plan implements the first real pipeline runtime described in
[`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md).
It is written for an engineer who is good at Python but has little or no Weft
or SimpleBroker context.

Read this as a strict execution document, not as a sketch. The likely failure
modes are:

- inventing a second durable path instead of extending the current one,
- over-generalizing into a graph engine or workflow SDK,
- hiding runtime state in ad hoc tables or opaque side channels,
- treating `weft.log.tasks` as the primary pipeline recovery substrate instead
  of using queue truth plus a bounded runtime registry,
- reusing the wrong existing class because it looks close enough,
- and writing mock-heavy tests that never prove the real queue and process
  behavior.

Do not do those things.

## Goal

Replace the current Phase 0 CLI-owned pipeline loop with a first-class linear
pipeline runtime that stays on Weft's existing durable spine:

`TaskSpec -> Manager -> task process -> queues/state log`

The new runtime should make `weft run --pipeline ...` create a pipeline task
with its own TID, public `inbox` / `outbox` / `ctrl_in` / `ctrl_out`, a public
`status` queue, generated edge tasks for stage handoff and checkpointing,
pre-started waiting child tasks, fail-fast semantics, and queue-visible status
snapshots that separate durable task `status` from derived live `activity`. It
must still feel like Weft, not like a second orchestration platform.

Mental model for the implementer:

- `inbox` / `outbox` are the data plane, analogous to `stdin` / `stdout`
- `ctrl_in` / `ctrl_out` are the public control plane, closest to structured
  signals plus `stderr`
- `P{pipeline_tid}.status` is the retained public status plane
- `P{pipeline_tid}.events` is the private owner-coordination plane
- `weft.log.tasks` is the durable audit plane

If a change starts mixing those planes, it is probably wrong.

## Scope Lock

This plan is intentionally split into one main implementation slice and two
follow-on slices.

The main slice implements:

- Phase 1 from [`12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md):
  first-class linear pipeline runs
- generated edge tasks
- pre-started waiting stage and edge children
- fail-fast control semantics
- `weft run --pipeline ...` with `--wait` and `--no-wait`
- final result retrieval through the existing `weft result` surface
- pipeline-owned status snapshots on `P{pipeline_tid}.status`
- activity-aware task snapshots and process-title details so pre-started
  waiters do not look like active work merely because they are alive

The main slice does **not** implement:

- `tee`, `tap`, or arbitrary graph authoring
- broker alias management as a first-class feature
- nested pipelines as stage targets
- pipeline-wide retry policy
- pipeline-wide resource limits
- autostart pipeline targets
- rich `weft status` / `weft task list` pipeline summaries
- automatic same-TID pipeline respawn after task-process death

The last point matters. The spec describes reconciliation and replay semantics,
but the current codebase does not have a generic child-task restart facility.
This plan implements the queue and metadata shape required for later replay,
manual inspection, and explicit reconciliation. It does **not** add automatic
pipeline recovery, manager-level child respawn, or runtime orphan adoption in
the first merge. If product requirements harden around same-TID automatic
replay in this same change, stop and write a separate manager supervision plan
first.

## Source Documents

Primary specs:

- [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
  [PL-0.1], [PL-0.2], [PL-1.1], [PL-1.2], [PL-1.3], [PL-1.4], [PL-1.5],
  [PL-2.3], [PL-2.4], [PL-2.5], [PL-2.6], [PL-2.7], [PL-3.2], [PL-4.1],
  [PL-4.2], [PL-4.3], [PL-4.4], [PL-4.5], [PL-5.1], [PL-5.2], [PL-5.3],
  [PL-5.4], [PL-5.5], [PL-7]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-4], [MF-5], [MF-6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.1], pipeline UX section
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.1], [CC-2.2], [CC-2.3], [CC-2.4], [CC-2.5]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.3]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMMUT.1], [IMMUT.2], [IMMUT.3], [STATE.1], [STATE.2], [QUEUE.1],
  [QUEUE.2], [QUEUE.3], [QUEUE.4], [QUEUE.5], [QUEUE.6], [OBS.1], [OBS.2],
  [OBS.3], [MANAGER.4], [IMPL.5], [IMPL.6]
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Product context:

- [`README.md`](../../README.md)
- [`../simplebroker/README.md`](../../../simplebroker/README.md)

Current implementation paths to read before editing:

- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/commands/specs.py`](../../weft/commands/specs.py)
- [`weft/commands/result.py`](../../weft/commands/result.py)
- [`weft/commands/status.py`](../../weft/commands/status.py)
- [`weft/commands/tasks.py`](../../weft/commands/tasks.py)
- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/core/launcher.py`](../../weft/core/launcher.py)
- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/core/tasks/monitor.py`](../../weft/core/tasks/monitor.py)
- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)
- [`tests/commands/test_run.py`](../../tests/commands/test_run.py)

## Read This First

Read these in order before editing:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/agent-context/README.md`](../agent-context/README.md)
3. [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
4. [`docs/agent-context/principles.md`](../agent-context/principles.md)
5. [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
6. [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
7. [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
8. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
9. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
10. [`weft/commands/run.py`](../../weft/commands/run.py)
11. [`weft/core/manager.py`](../../weft/core/manager.py)
12. [`weft/core/launcher.py`](../../weft/core/launcher.py)
13. [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
14. [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
15. [`weft/core/tasks/monitor.py`](../../weft/core/tasks/monitor.py)
16. [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)

Also inspect the current SimpleBroker queue and watcher style before creating
new runtime code:

- [`../simplebroker/simplebroker/sbqueue.py`](../../../simplebroker/simplebroker/sbqueue.py)
- [`../simplebroker/simplebroker/watcher.py`](../../../simplebroker/simplebroker/watcher.py)

You are not copying SimpleBroker code. You are learning the local style:

- small, direct modules
- concrete queue ownership
- explicit cleanup
- real broker-backed tests
- very little magic

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready.

1. Which function today is the only shared path that writes spawn requests for
   `weft run`?
   Answer: `_enqueue_taskspec()` in
   [`weft/commands/run.py`](../../weft/commands/run.py).

2. Which layer decides the child task class today?
   Answer: `Manager._launch_child_task()` in
   [`weft/core/manager.py`](../../weft/core/manager.py), and it currently
   hard-codes `Consumer`.

3. Which queue/log is the public proof that a task reached a terminal state?
   Answer: `weft.log.tasks`, not PID liveness and not `ctrl_out`.

4. Why is `Monitor` not the right edge implementation as-is?
   Answer: `Monitor` reserves directly into the downstream queue and therefore
   does not preserve an edge-local reserved queue for recovery and inspection.

5. Why can the new pipeline runtime not import command helpers directly from
   `weft/commands/run.py`?
   Answer: runtime code must not depend on CLI modules for its canonical core
   path. Shared helpers belong under `weft/core/`.

## Fixed Design Decisions

These decisions are already made. They are not open during implementation.

### 1. Keep the existing durable spine

Pipelines, stages, and generated edges must all stay on Weft's current durable
execution spine:

`TaskSpec -> Manager -> task process -> queues/state log`

Do not add:

- a direct `launch_task_process()` call from CLI pipeline code,
- a second orchestrator loop outside tasks,
- or a hidden table that becomes more authoritative than the queues.

### 2. Keep public `TaskSpec` leaf-shaped

User-authored `TaskSpec.spec.type` stays the existing leaf vocabulary. Do not
add a public `"pipeline"` or `"edge"` type.

Internal runtime-owned tasks may use a reserved metadata key to select their
task class. That internal dispatch must stay small and local.

### 3. Use human-readable concrete queue names, not broker alias management

This slice should not add a broker alias table or alias CLI.

Instead:

- pipeline public queues use `P{pipeline_tid}.<name>`
- edge handoff queues use `P{pipeline_tid}.{edge_name}`
- child task TIDs remain normal `T{tid}` values
- bindings are recorded in pipeline runtime metadata and snapshots

This keeps the human affordance without opening a second alias feature area.

### 4. The pipeline task does not copy stage results between queues

The pipeline task coordinates and reports. It does **not** sit in the middle
reading stage results and writing the next stage input.

Edge tasks own stage-to-stage transfer and checkpoint emission.

### 5. The pipeline task does not consume the public pipeline inbox

The public pipeline inbox is part of the pipeline data plane.

For the first-class runtime:

- the entry edge consumes `P{pipeline_tid}.inbox`
- inter-stage edges consume stage outboxes
- the exit edge writes into `P{pipeline_tid}.outbox`
- the pipeline task itself watches control and private progress surfaces

This matters because it avoids a pointless copy of the initial payload.

### 6. `ctrl_out` is control reply, `status` is retained public status

Use the surfaces like this:

- `ctrl_in`: public commands into the pipeline task
- `ctrl_out`: public control replies
- `P{pipeline_tid}.status`: retained live pipeline status snapshots and
  terminal summary
- `P{pipeline_tid}.events`: private child-to-owner coordination
- `weft.log.tasks`: durable audit trail

Why:

- Unix-like systems keep the data plane separate from chatter and progress
- Weft keeps caller-facing data on `outbox`
- pipelines are a higher-order orchestration object, so they get one extra
  public observability surface instead of overloading `ctrl_out`
- child stage and edge reports do **not** belong on that public surface

Do not route stage or edge owner events through `ctrl_out`.

### 7. Pre-start waiting children are acceptable, but the UX must stay honest

Stage and edge child tasks may be launched before work arrives.

Do not try to optimize this away in the first implementation. The cost of one
waiting task process per stage/edge is acceptable in this slice. Large graphs
can be handled later if they become a real problem.

The reporting rule is strict:

- durable task `status` stays the real lifecycle field
- derived `activity` explains what a live task is doing right now
- pre-started children should therefore read as `status=running`,
  `activity=waiting`, not as active computation

### 8. Fail-fast is the default

If a stage fails, the pipeline fails.
If an edge fails, the pipeline fails.

Do not add retry, continue-on-error, or branch policy knobs in this slice.

### 9. Edge checkpoint emission is best-effort after the move

For ordinary linear handoff:

- move or write the downstream payload first
- emit the checkpoint second

If checkpoint emission is lost after the durable move, recovery must reconcile
queue state. Do **not** fail the edge just because the progress event could not
be published after the move already happened.

### 10. Stage-local resource limits only

Keep the existing per-task `limits`.
Do not add aggregate pipeline limits or inheritance rules.

### 11. No automatic same-TID replay in the first merge

This plan builds the runtime in a way that a later replay/resume facility can
use correctly:

- stable stage and edge identities
- stable child TID assignment per pipeline run
- reconcilable queue state
- explicit checkpoints and bindings

It does **not** add a manager child-restart loop in this same change.

### 12. A pipeline run is one input message and one final result

In this slice, one pipeline run corresponds to one initial payload and one
final caller-facing result.

That means:

- the entry edge is a one-shot waiter
- later writes to `P{pipeline_tid}.inbox` are out of contract
- stages are compiled for a single pass through the linear chain

Do not quietly turn the first runtime slice into a multi-message service.

### 12. The full runtime plan is compiled once at submission time

For the first merge:

- compile the full runtime plan in the submission path
- store that precompiled plan under one reserved metadata key
- have the pipeline task load that exact precompiled plan at bootstrap

Do not re-read stored task specs from disk during runtime bootstrap.
Do not make child TID assignment depend on runtime races.

Child TIDs should therefore be chosen up front, at pipeline submission time,
against the active broker context, then stored in the precompiled runtime plan.

## Context and Key Files

### A. User-facing pipeline spec and validation

Current structure:

- [`weft/commands/run.py`](../../weft/commands/run.py) `_load_pipeline_spec()` loads
  raw JSON into a plain dict.
- [`weft/commands/specs.py`](../../weft/commands/specs.py) only checks that a
  pipeline payload has `stages`.

Files to modify:

- new `weft/core/pipelines.py`
- `weft/commands/specs.py`
- `weft/commands/run.py`

Read first:

- [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
  [PL-2]
- [`weft/commands/specs.py`](../../weft/commands/specs.py)
- [`weft/core/taskspec.py`](../../weft/core/taskspec.py)

Required reuse:

- Pydantic models and validation patterns from
  [`weft/core/taskspec.py`](../../weft/core/taskspec.py)

### B. Spawn request submission

Current structure:

- `_enqueue_taskspec()` in [`weft/commands/run.py`](../../weft/commands/run.py)
  writes spawn requests with an exact timestamp equal to the task TID.
- it also injects `WORK_ENVELOPE_START` when `inbox_message` is `None` for a
  non-persistent task

That helper is CLI-owned today. The pipeline runtime will also need it.

Files to modify:

- new `weft/core/spawn_requests.py`
- `weft/commands/run.py`

Read first:

- [`weft/commands/run.py`](../../weft/commands/run.py)
- [`weft/core/manager.py`](../../weft/core/manager.py) `_build_child_spec()`
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-6]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [MANAGER.4]

Required reuse:

- exact-timestamp spawn-request write semantics
- existing best-effort spawn-request deletion on setup failure

### C. Manager child-task launch seam

Current structure:

- `Manager._build_child_spec()` validates a child TaskSpec and assigns the
  spawn-request timestamp as its TID.
- `Manager._launch_child_task()` always launches `Consumer`.

Files to modify:

- `weft/core/manager.py`
- `weft/core/launcher.py`
- `weft/_constants.py`
- `weft/core/tasks/__init__.py`
- new `weft/core/tasks/pipeline.py`

Read first:

- [`weft/core/manager.py`](../../weft/core/manager.py)
- [`weft/core/launcher.py`](../../weft/core/launcher.py)
- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)

Required reuse:

- the same `launch_task_process()` path
- the same resolved TaskSpec validation path
- the same `parent_tid` metadata convention

### D. Runtime task classes

Current structure:

- `BaseTask` owns queue wiring, control handling, state logging, and process
  titles.
- `Consumer` owns actual work-item execution.
- `Monitor` is the closest existing forwarding task, but it moves directly into
  the downstream queue and does not keep an edge-local reserved queue.

Files to modify:

- new `weft/core/tasks/pipeline.py`
- `weft/core/tasks/__init__.py`

Read first:

- [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- [`weft/core/tasks/monitor.py`](../../weft/core/tasks/monitor.py)
- [`weft/core/tasks/multiqueue_watcher.py`](../../weft/core/tasks/multiqueue_watcher.py)

Required reuse:

- `BaseTask` control handling and `_report_state_change()`
- shared queue handle reuse through `BaseTask._queue()`
- generator-based log reads through `iter_queue_json_entries()`

Do **not** reuse `Monitor` as the final edge implementation.

### E. Result, control, and status surfaces

Current structure:

- [`weft/commands/result.py`](../../weft/commands/result.py) uses task-local
  outbox and ctrl queues plus `weft.log.tasks`.
- [`weft/commands/tasks.py`](../../weft/commands/tasks.py) resolves `ctrl_in`
  from logged TaskSpec IO.
- [`weft/commands/status.py`](../../weft/commands/status.py) rebuilds snapshots
  from `weft.log.tasks`; the narrow pipeline reader augments that with
  `P{pipeline_tid}.status` snapshots.

Files to modify:

- `weft/commands/result.py`
- `weft/commands/tasks.py`
- `weft/commands/status.py`
- possibly new `weft/commands/_pipeline_status.py`

Read first:

- [`weft/commands/result.py`](../../weft/commands/result.py)
- [`weft/commands/_result_wait.py`](../../weft/commands/_result_wait.py)
- [`weft/commands/status.py`](../../weft/commands/status.py)
- [`weft/commands/tasks.py`](../../weft/commands/tasks.py)

Required reuse:

- existing one-shot result waiter
- existing `ctrl_in` resolution from logged TaskSpec IO
- existing task log snapshots for fallback

### F. Tests and harnesses

Use the real test harnesses. Do not fake the broker or the task lifecycle.

Primary files:

- [`tests/helpers/weft_harness.py`](../../tests/helpers/weft_harness.py)
- [`tests/conftest.py`](../../tests/conftest.py)
- [`tests/cli/test_cli_pipeline.py`](../../tests/cli/test_cli_pipeline.py)
- [`tests/commands/test_run.py`](../../tests/commands/test_run.py)
- `tests/core/test_manager.py`
- new `tests/core/test_pipelines.py`
- new `tests/tasks/test_pipeline_runtime.py`
- `tests/commands/test_result.py`
- `tests/commands/test_task_commands.py`
- `tests/commands/test_status.py`

Use:

- `run_cli(...)` for end-to-end CLI flows
- `WeftTestHarness` for manager, worker, and control behavior
- real queue history reads through `iter_queue_json_entries()`

Do **not** mock:

- `simplebroker.Queue`
- the manager lifecycle
- task reservation semantics
- `weft.log.tasks`
- or process spawning

One narrow exception is allowed:

- you may monkeypatch an auxiliary checkpoint-emission helper to force a
  post-move progress-report failure, because that is an auxiliary failure mode,
  not the main durable path

## Invariants and Constraints

- Keep `TaskSpec.spec` and `TaskSpec.io` immutable after resolved TaskSpec
  creation. Preserve [IMMUT.1], [IMMUT.2], [IMMUT.3].
- Keep state transitions forward-only. Preserve [STATE.1] and [STATE.2].
- Keep the default TaskSpec queue naming rule unchanged. Preserve [QUEUE.3].
  Pipeline public `P{tid}` queues are explicit IO overrides on internal runtime
  TaskSpecs, not a change to global naming rules.
- Keep queue claim/move semantics correct. Preserve [QUEUE.4], [QUEUE.5],
  and [QUEUE.6].
- Keep `spawn` process creation. Preserve [IMPL.5] and [IMPL.6].
- Keep `weft.log.tasks` as the durable audit trail. Preserve [OBS.1], [OBS.2],
  and [OBS.3].
- Add `weft.state.pipelines` as a runtime-state queue, not as a dump-persisted
  control table.
- Keep child TID correlation through spawn-request timestamps. Preserve
  [MANAGER.4].
- Do not mutate resolved stage child TaskSpecs at runtime after validation.
- Do not create a new external dependency.
- Do not add a generic plugin registry for internal task classes.
- Do not add a new top-level `weft pipeline ...` verb family.
- Do not add queue cleanup redesigns in this slice.
- Do not implement autostart pipelines in the same first merge.
- If the serialized precompiled runtime plan would exceed the active broker
  max-message-size limit, fail clearly instead of truncating or silently
  spilling it.

## Hidden Couplings

These are the couplings most likely to bite a zero-context implementer.

### 1. `WORK_ENVELOPE_START` is wrong for pipeline runtime waiters

The current `_enqueue_taskspec()` helper injects `WORK_ENVELOPE_START` for
ordinary non-persistent tasks when no `inbox_message` is provided.

That is correct for direct tasks and wrong for:

- the pipeline task itself when it has no initial input,
- pre-started stage waiters,
- and pre-started edge waiters

If you reuse that helper unchanged, you will accidentally start pipelines with
fake input.

### 2. Manager launch currently hard-codes `Consumer`

If you try to implement internal pipeline and edge tasks without changing the
manager launch seam, you will either:

- bypass the manager and violate the durable spine,
- or wedge pipeline logic into `Consumer` and pollute the leaf task runtime

Both are wrong.

### 3. `Monitor` is tempting and wrong

`Monitor` looks close to an edge because it forwards messages.
It is still the wrong base implementation because it does not keep an
edge-local reserved queue. If you build edges on top of it unchanged, you lose
the recovery surface the spec depends on.

### 4. `weft result` depends on logged TaskSpec IO names

`weft result <tid>` already respects custom queue names **if** the logged
TaskSpec snapshot has them.

That means the internal pipeline task spec must carry its public `P{tid}`
queues in `io`, and the pipeline task must continue using normal task log
events. Do not hide those queue names in a side table only.

### 5. `weft task status` is still log-driven

Today `task status` reconstructs state from `weft.log.tasks`.
It only knows about pipeline-specific snapshots when the narrow
`P{pipeline_tid}.status` reader is invoked for pipeline TIDs.

Do not replace that with a second live query path in this slice just to make
pipelines work. Instead:

- keep the log-driven reader
- teach the existing task snapshot path to preserve optional derived
  `activity` and `waiting_on` fields from the latest event
- keep pipeline `P{pipeline_tid}.status` snapshots as the richer
  pipeline-owned surface for the narrow pipeline-TID reader

### 6. Pipeline progress is not the same as child task durable state

A waiting child task process may be alive before it has processed any work.
Pipeline status must therefore separate:

- durable task `status`
- derived live `activity`
- pipeline-level explanation such as `waiting_on`

Do not try to force raw child task `status` to carry the whole pipeline
explanation.

### 7. Stage defaults can change transport semantics

`defaults.input` on a downstream stage is not just metadata. It changes the
actual payload that should flow into that stage.

Ordinary edges can zero-copy move.
An edge that applies `defaults.input` cannot. It must write the override value.

### 8. Recovery is reconciliation, not blind trust

The spec explicitly allows checkpoint lag behind true queue state.
Implementation must therefore preserve enough queue-local truth that later
reconciliation can explain what happened.

Do not treat checkpoint history as globally authoritative.

The current slice does **not** implement automatic recovery or replay.
It only preserves the inputs that later recovery work or manual inspection
would need:

- the active runtime registry in `weft.state.pipelines`
- the owned edge and stage queues named by that registry
- live task and process liveness surfaces

`weft.log.tasks` remains useful for audit and terminal detail, but it is not
the primary reconciliation substrate for pipelines.

### 9. Use `weft.state.pipelines` as the active runtime registry

The first-class runtime should own one runtime-state queue record per live
pipeline run.

That record must be enough to tell a zero-context operator or later recovery
tool:

- which pipeline run is active
- which stage and edge child TIDs belong to it
- which concrete `P{pipeline_tid}.*` queues belong to it
- which logical edge names map to those concrete queues
- which saved pipeline name or path the run came from

The record is a recovery breadcrumb, not the primary live status view.
`P{pipeline_tid}.status` remains the live status surface.

Cleanup rule:

- write the record during bootstrap before the pipeline begins depending on
  child progress
- delete the record only as the last clean terminal cleanup step, after
  terminal status has been published and after any final public outbox result
  is durably available
- stale records after crash are intentional and should be left for inspection

### 10. The compile boundary must stay explicit

The CLI-side pipeline builder should validate the human pipeline spec and build
the full runtime plan once.

The pipeline task should then load that precompiled runtime plan from reserved
metadata. It should not re-resolve stored task specs from disk during runtime
bootstrap.

This keeps the run definition stable and keeps compile bugs out of the hot
control path.

## Rollout and Rollback

### Rollout

Land this in dependency order. Do not invert it.

1. Shared validation and spawn helpers.
2. Internal task-class routing.
3. Runtime compiler and task classes behind direct tests.
4. CLI switch from Phase 0 to the first-class runtime.
5. Minimal result/control proof.
6. Optional pipeline-specific `task status` reader.
7. Docs and cleanup.

Keep the current sequential `_run_pipeline()` path alive until the new runtime
passes the targeted tests. Remove it only after the new runtime is green.

### Rollback

Rollback is clean only if you preserve the old CLI-owned pipeline helper until
the final CLI switch.

If the new runtime path proves unstable:

- keep the new core helpers and runtime classes behind tests,
- switch `weft run --pipeline` back to the Phase 0 sequential runner,
- and defer the CLI cutover

Do not ship both runtime paths as long-term supported behavior.

## Implementation Tasks

### 1. Create the canonical pipeline model and validation path

- Outcome:
  one shared module owns the pipeline spec model, structural validation,
  stage-default merge rules, and runtime-owned metadata models
- Files to touch:
  - new `weft/core/pipelines.py`
  - `weft/commands/specs.py`
  - `weft/commands/run.py`
  - new `tests/core/test_pipelines.py`
- Read first:
  - [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
    [PL-1], [PL-2]
  - [`weft/commands/specs.py`](../../weft/commands/specs.py)
  - [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
- Required changes:
  - add a concrete `PipelineSpec` model and `PipelineStage` model
  - validate:
    - `stages` exists and is non-empty
    - every stage is an object
    - every stage has a unique `name`
    - every stage has a non-empty stored task reference in `task`
    - `defaults` only accepts the supported keys from the spec
  - create explicit runtime-owned metadata models for:
    - the pipeline task runtime config
    - the edge task runtime config
    - the pipeline-owned child owner config
    - any per-stage compiled binding records needed by runtime code
  - define the reserved metadata keys explicitly in one place, for example:
    - `_weft_pipeline_runtime`
    - `_weft_edge_runtime`
    - `_weft_pipeline_owner`
  - move `_load_pipeline_spec()` to use this module instead of raw dict logic
  - change `spec create`, `spec validate`, and `spec generate` to use the same
    validator and example shape
- Tests to write first:
  - `test_pipeline_spec_requires_non_empty_stages`
  - `test_pipeline_spec_rejects_duplicate_stage_names`
  - `test_pipeline_spec_requires_task_reference`
  - `test_validate_spec_uses_shared_pipeline_validator`
- Stop and re-evaluate if:
  - this module starts growing graph or fan-out concepts
  - validation starts loading runtime state or touching the broker
- Done signal:
  - pipeline shape is validated in one shared place
  - `weft/commands/run.py` and `weft/commands/specs.py` no longer hand-roll
    pipeline dict checks

### 2. Extract shared spawn-request submission into `weft/core/`

- Outcome:
  runtime code and CLI code share one canonical spawn-request write path, with
  explicit control over whether a missing input should be replaced by
  `WORK_ENVELOPE_START`
- Files to touch:
  - new `weft/core/spawn_requests.py`
  - `weft/commands/run.py`
  - `tests/commands/test_run.py`
- Read first:
  - [`weft/commands/run.py`](../../weft/commands/run.py)
  - [`weft/core/manager.py`](../../weft/core/manager.py)
  - [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
    [MF-6]
- Required changes:
  - move exact-timestamp spawn-request writing out of `weft/commands/run.py`
  - expose a helper that accepts:
    - a TaskSpec template
    - an optional input payload
    - an explicit TID or generated TID
    - a flag like `seed_start_envelope`
  - keep the current ordinary-task behavior as the default:
    `seed_start_envelope=True`
  - use `seed_start_envelope=False` for:
    - the pipeline task itself when no initial input exists
    - pre-started stage children
    - pre-started edge children
  - make the TID contract explicit in the helper:
    - if an explicit TID is supplied, force the spawn-request message timestamp
      to that exact value
    - the manager must continue using the consumed spawn-request message ID as
      the child task TID without rewriting it
    - tests must prove that a precompiled child TID and the launched child TID
      are the same value
  - move best-effort spawn-request deletion into the shared helper or a nearby
    shared helper
- Tests to write first:
  - update the existing "delete spawn request when ensure_manager fails" tests
    to keep covering the shared path
  - add a focused proof that a no-input internal waiter spawn does **not** get
    `WORK_ENVELOPE_START`
  - add a focused proof that an explicit TID survives round-trip through the
    spawn request and manager launch path unchanged
- Stop and re-evaluate if:
  - runtime code starts importing `weft/commands/*`
  - a second direct child-launch path appears
- Done signal:
  - `weft/commands/run.py` is a thin wrapper over a shared spawn helper
  - runtime code can submit children without fake startup envelopes

### 3. Add a minimal internal task-class routing seam

- Outcome:
  the manager can launch `Consumer` by default or one of two reserved internal
  task classes for pipeline runtime work
- Files to touch:
  - `weft/_constants.py`
  - `weft/core/manager.py`
  - `weft/core/launcher.py`
  - `weft/core/tasks/__init__.py`
  - new `weft/core/tasks/pipeline.py`
  - `tests/core/test_manager.py`
- Read first:
  - [`weft/core/manager.py`](../../weft/core/manager.py)
  - [`weft/core/launcher.py`](../../weft/core/launcher.py)
  - [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
- Required changes:
  - define a reserved metadata key in `_constants.py`, for example
    `"_weft_runtime_task_class"`
  - define exactly two reserved values in this slice:
    - `"pipeline"`
    - `"pipeline_edge"`
  - add a small local resolver in the manager launch path:
    - absent -> `Consumer`
    - `"pipeline"` -> `PipelineTask`
    - `"pipeline_edge"` -> `PipelineEdgeTask`
    - anything else -> reject the child spec loudly and log why
  - keep the import boundary simple and avoid circular imports. If the cleanest
    implementation needs a local import inside the resolver, do that. Do not
    answer a circular-import problem by inventing a registry framework.
  - keep `metadata.role` as a separate observability field, not the dispatch key
  - do **not** add a plugin registry, dynamic import string, or generic task
    factory protocol
- Tests to write first:
  - `test_manager_launches_consumer_when_no_internal_task_class_is_set`
  - `test_manager_launches_pipeline_task_for_reserved_internal_class`
  - `test_manager_rejects_unknown_internal_task_class`
- Stop and re-evaluate if:
  - you start building a generic runtime registry
  - the launch path stops going through `launch_task_process()`
- Done signal:
  - internal runtime-owned task classes can be launched through the same manager
    path as normal child tasks

### 4. Build the concrete linear pipeline compiler

- Outcome:
  one helper compiles a validated `PipelineSpec` into:
  - a pipeline task TaskSpec
  - concrete queue names
  - stage child TaskSpecs
  - edge child TaskSpecs
  - stable child TIDs keyed by stage and edge identity
- Files to touch:
  - `weft/core/pipelines.py`
  - `weft/commands/run.py`
  - `tests/core/test_pipelines.py`
- Read first:
  - [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
    [PL-2.4], [PL-2.5], [PL-2.6], [PL-3.2], [PL-4.1]
  - [`weft/core/taskspec.py`](../../weft/core/taskspec.py)
  - [`weft/commands/run.py`](../../weft/commands/run.py) current stage defaults logic
- Required changes:
  - define the public pipeline queue namespace:
    - `P{pipeline_tid}.inbox`
    - `P{pipeline_tid}.outbox`
    - `P{pipeline_tid}.ctrl_in`
    - `P{pipeline_tid}.ctrl_out`
    - `P{pipeline_tid}.status`
    - `P{pipeline_tid}.events`
  - define the concrete inter-stage handoff queue namespace:
    - `P{pipeline_tid}.{edge_name}`
  - preserve the ordinary `T{tid}.reserved` queue for every child task
  - make the compiler choose child TIDs up front, at pipeline submission time,
    against the active broker context so the pipeline task knows the child
    identities before launch
  - generate those child TIDs through the broker timestamp path, not through
    local `time.time_ns()` calls, so ordering and format stay broker-valid
  - build the internal pipeline task spec with:
    - public `P{pipeline_tid}` queues in `io`
    - reserved metadata for the full precompiled runtime plan
    - reserved internal task class `"pipeline"`
    - `metadata.role = "pipeline"`
  - build stage child TaskSpecs from stored task specs:
    - load by saved task name
    - apply stage defaults and current `io_overrides` merge rules first
    - then overwrite pipeline-owned input routing with the compiled edge queue
    - add reserved pipeline-owner metadata with:
      - `pipeline_tid`
      - `events_queue`
      - `role = "pipeline_stage"`
      - `stage_name`
    - set `metadata.parent_tid = pipeline_tid`
    - set `metadata.role = "pipeline_stage"`
  - build edge child TaskSpecs with:
    - reserved internal task class `"pipeline_edge"`
    - reserved metadata for edge runtime config
    - reserved pipeline-owner metadata with:
      - `pipeline_tid`
      - `events_queue`
      - `role = "pipeline_edge"`
      - `edge_name`
    - `metadata.parent_tid = pipeline_tid`
    - `metadata.role = "pipeline_edge"`
  - validate stage compatibility for the first runtime slice:
    - reject nested pipeline targets
    - reject persistent stage tasks
    - reject interactive stage tasks
    - reject streaming stage tasks
    - require the pipeline-stage success contract:
      - successful stage execution must emit exactly one handoff payload
      - `None` is allowed and becomes JSON `null`
      - large output is allowed when it is emitted as one spill/reference envelope
  - be explicit about queue ownership:
    - entry edge consumes `P{pipeline_tid}.inbox`
    - inter-stage edges consume stage outboxes
    - exit edge writes `P{pipeline_tid}.outbox`
    - pipeline task itself watches `ctrl_in` and `P{pipeline_tid}.events`
  - make the compiled edge naming rule explicit:
    - derive one logical edge name from pipeline structure, for example
      `extract-to-transform`
    - compile that name into the concrete queue
      `P{pipeline_tid}.extract-to-transform`
    - do not invent a second unrelated edge queue naming scheme at runtime
- Tests to write first:
  - `test_pipeline_compiler_builds_public_p_queues`
  - `test_pipeline_compiler_assigns_stable_child_tids_by_stage_and_edge_name`
  - `test_pipeline_compiler_overwrites_stage_input_queue_with_edge_binding`
  - `test_pipeline_compiler_rejects_persistent_stage_task`
  - `test_pipeline_compiler_rejects_interactive_stage_task`
  - `test_pipeline_compiler_rejects_streaming_stage_task`
- Stop and re-evaluate if:
  - you start mutating resolved TaskSpecs after validation
  - or if the compiler tries to read live queue state
- Done signal:
  - one function builds the complete runtime plan without touching the broker
  - no later task has to re-derive queue names or child identities

### 5. Add compact owner-event emission for pipeline-owned children

- Outcome:
  pipeline-owned stage and edge tasks can emit compact terminal events to the
  owning pipeline's private events queue without extending the public
  `TaskSpec` model
- Files to touch:
  - `weft/_constants.py`
  - `weft/core/tasks/base.py`
  - `weft/core/tasks/consumer.py`
  - new `tests/tasks/test_pipeline_runtime.py`
- Read first:
  - [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
    [PL-3.2], [PL-4.1], [PL-4.2]
  - [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
  - [`weft/core/tasks/consumer.py`](../../weft/core/tasks/consumer.py)
- Required changes:
  - define one reserved metadata contract for pipeline-owned children, for
    example `_weft_pipeline_owner`
  - make that metadata compile-time config only; do not mutate it at runtime
  - add one small helper on the task runtime side that:
    - reads the owner metadata if present
    - writes compact owner events to `P{pipeline_tid}.events`
    - no-ops cleanly when the task is not pipeline-owned
  - for pipeline-owned stage tasks:
    - emit a compact terminal success event only after the stage's one handoff
      payload is durably written to outbox
    - emit a compact terminal failure event on task failure
    - emit stop/kill terminal events when those are the terminal outcomes
  - for pipeline-owned edge tasks:
    - emit compact failure terminal events through the same helper
    - keep successful handoff reporting on the dedicated `edge_checkpoint`
      payload rather than a generic success event
  - keep owner events compact. They are not a second full task log and should
    not embed full TaskSpec snapshots
- Tests to write first:
  - `test_pipeline_owned_stage_emits_success_owner_event_after_outbox_write`
  - `test_pipeline_owned_stage_emits_failure_owner_event`
  - `test_non_pipeline_task_emits_no_owner_event`
- Stop and re-evaluate if:
  - the helper starts back-writing runtime state into TaskSpec metadata
  - or if it starts mirroring full task-log payloads into the pipeline events
    queue
- Done signal:
  - pipeline-owned children can report compact terminal events to the owning
    pipeline without using `weft.log.tasks`

### 6. Implement `PipelineEdgeTask`

- Outcome:
  one internal edge task type owns linear handoff and checkpoint emission
- Files to touch:
  - `weft/_constants.py`
  - new `weft/core/tasks/pipeline.py`
  - `weft/core/tasks/__init__.py`
  - `tests/tasks/test_pipeline_runtime.py`
- Read first:
  - [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
    [PL-3.2], [PL-4.1], [PL-4.3]
  - [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
  - [`weft/core/tasks/monitor.py`](../../weft/core/tasks/monitor.py)
- Required changes:
  - implement one `PipelineEdgeTask` class with two explicit source modes:
    - `pipeline_input`
    - `stage_output`
  - for `pipeline_input`:
    - reserve from `P{pipeline_tid}.inbox` into the edge task's own reserved queue
    - fast path: move the payload directly to the downstream handoff queue
    - override path: if downstream `defaults.input` exists, write that override
      payload instead of moving the original body
  - for `stage_output`:
    - reserve one payload from the upstream stage outbox into the edge task's
      own reserved queue
    - move that payload directly into the downstream queue
    - if downstream `defaults.input` exists, consume the upstream payload but
      write the override payload downstream instead of forwarding the original
      body
    - do not tail `weft.log.tasks`
    - do not aggregate multi-message output
    - rely on the compile-time stage contract that successful
      pipeline-compatible stages emit exactly one handoff payload
  - after the downstream queue durably owns the next-stage payload:
    - emit the edge checkpoint to `P{pipeline_tid}.events`
    - do **not** claim the downstream stage has already reserved it
  - if the checkpoint write fails after a successful move or downstream write:
    - log/debug it
    - preserve the successful transfer
    - do not mark the edge failed solely because the report write was lost
  - keep an edge-local reserved queue so later reconciliation can inspect it
  - make the reserved-queue contract explicit in code comments and tests:
    - both source modes reserve the source payload before downstream handoff
  - use normal task control handling for STOP and KILL
- Tests to write first:
  - `test_entry_edge_moves_pipeline_input_and_emits_checkpoint`
  - `test_edge_uses_override_payload_when_downstream_defaults_input_is_set`
  - `test_stage_output_edge_moves_single_payload_without_rewriting_it`
  - `test_stage_output_edge_consumes_upstream_payload_when_override_input_is_set`
  - `test_edge_keeps_successful_handoff_when_checkpoint_emit_fails`
- Stop and re-evaluate if:
  - the edge starts calling CLI helpers or printing output
  - or if the edge starts reasoning about global task state
  - or if you are tempted to make the pipeline task itself perform the handoff
- Done signal:
  - edge checkpoints mean exactly what [PL-4.1] says they mean
  - ordinary one-message handoff uses the no-pointless-copy fast path

### 7. Implement `PipelineTask`

- Outcome:
  one internal pipeline task owns child bootstrap, unit-level state, fail-fast
  control, and pipeline status snapshots
- Files to touch:
  - new `weft/core/tasks/pipeline.py`
  - `weft/core/tasks/__init__.py`
  - `tests/tasks/test_pipeline_runtime.py`
- Read first:
  - [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
    [PL-3.2], [PL-4.2], [PL-4.4], [PL-5.1], [PL-5.2], [PL-5.3]
  - [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
  - [`weft/core/manager.py`](../../weft/core/manager.py)
  - new `weft/core/spawn_requests.py`
  - new `weft/core/pipelines.py`
- Required changes:
  - implement `PipelineTask` as a `BaseTask` subclass
  - do **not** perform complex spawn writes in `__init__`
  - instead, add a one-time bootstrap step that runs from the task loop
  - on bootstrap:
    - load the full precompiled runtime plan from reserved metadata
    - write the active pipeline registry record to `weft.state.pipelines`
      before depending on child work
    - enqueue child spawn requests for all stages and edges using the shared
      spawn helper with `seed_start_envelope=False`
    - record child TIDs and bindings in the in-memory pipeline snapshot
    - publish an initial status snapshot on `P{pipeline_tid}.status`
    - if bootstrap fails after some children were already enqueued, mark the
      pipeline failed and send `STOP` to the subset of child TIDs already known
      before exiting
  - the pipeline task should watch:
    - `ctrl_in`
    - `P{pipeline_tid}.events`
  - it should **not** consume `P{pipeline_tid}.inbox`
  - it should not attempt automatic recovery or reconciliation in this slice
  - it should not depend on `weft.log.tasks` for runtime coordination
- on a stage terminal owner event:
    - update the in-memory stage snapshot
    - if the status is failure-like, fail the pipeline and stop waiting
      children
    - if the status is success, mark that stage completed and leave handoff
      progress to the corresponding edge checkpoint
  - on an edge checkpoint:
    - update the in-memory stage/edge snapshot
    - do not use the edge checkpoint as the sole proof that the upstream stage
      completed successfully; that comes from the stage terminal owner event
    - keep durable pipeline `status` and derived pipeline `activity` separate
      in the snapshot shape
    - publish a fresh `type = "pipeline_status"` snapshot on
      `P{pipeline_tid}.status`
    - if the exit edge reported success, mark the pipeline task completed,
      publish a terminal summary, and stop waiting children
  - on any child failure event:
    - mark the pipeline task failed
    - publish a failing snapshot on `P{pipeline_tid}.status`
    - send `STOP` to waiting children
    - stop admitting further work
  - on `STATUS` control:
    - emit the current `type = "pipeline_status"` snapshot on
      `P{pipeline_tid}.status`
    - emit the normal `STATUS` ack on `ctrl_out`, including the status queue
      name if helpful
  - on `STOP` / `KILL` control:
    - propagate the control to live children through their task-local `ctrl_in`
    - mark the pipeline task terminal through normal task semantics
  - on any clean terminal path:
    - preserve any final public result in `P{pipeline_tid}.outbox`
    - publish the terminal snapshot on `P{pipeline_tid}.status`
    - delete the `weft.state.pipelines` record last
  - keep pipeline snapshots compact and close to the spec example
- Tests to write first:
  - `test_pipeline_task_bootstraps_all_children_before_any_stage_runs`
  - `test_pipeline_task_writes_active_registry_record_before_child_progress`
  - `test_pipeline_task_publishes_initial_waiting_activity_snapshot`
  - `test_pipeline_task_marks_stage_completed_on_stage_success_owner_event`
  - `test_pipeline_task_handles_stage_success_and_edge_checkpoint_in_either_order`
  - `test_pipeline_task_marks_completed_when_exit_edge_checks_in`
  - `test_pipeline_task_fails_fast_when_child_stage_fails`
  - `test_pipeline_task_stop_propagates_to_waiting_children`
  - `test_pipeline_task_kill_propagates_to_active_child`
  - `test_pipeline_task_deletes_active_registry_record_only_after_terminal_output`
- Stop and re-evaluate if:
  - the pipeline task starts reading and rewriting stage result payloads itself
  - or if it starts owning a second private state store
- Done signal:
  - a pipeline run is a real task with its own TID, queues, terminal state, and
    status snapshots

### 8. Surface derived live activity in task snapshots and process titles

- Outcome:
  pre-started watcher tasks remain honest in operator surfaces without
  widening the durable task state machine
- Files to touch:
  - `weft/core/tasks/base.py`
  - `weft/core/tasks/consumer.py`
  - new `weft/core/tasks/pipeline.py`
  - `weft/commands/status.py`
  - `weft/commands/tasks.py`
  - `tests/tasks/test_pipeline_runtime.py`
  - `tests/commands/test_status.py`
- Read first:
  - [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
    [CC-2.4]
  - [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
    [CLI-1.1.1], [CLI-1.2.1], `task status`
  - [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
    [PL-1.5], [PL-5.3]
  - [`weft/core/tasks/base.py`](../../weft/core/tasks/base.py)
  - [`weft/commands/status.py`](../../weft/commands/status.py)
  - [`weft/commands/tasks.py`](../../weft/commands/tasks.py)
- Required changes:
  - keep `TaskSpec.state.status` and the forward-only lifecycle state machine
    exactly as they are
  - add one small shared observability concept:
    - `activity`: derived live behavior such as `bootstrapping`, `waiting`,
      `working`, or `stopping`
    - `waiting_on`: optional queue or child surface that explains the wait
    - `activity` is live-only and optional. Terminal tasks should normally rely
      on `status` alone and omit `activity` and `waiting_on`
  - expose these through the existing reporting path rather than a second state
    store:
    - add one shared helper that updates the in-memory `(activity, waiting_on)`
      pair
    - have that helper emit a lightweight task-log event only when the pair
      changed
    - include the current pair in `_report_state_change(...)` payloads when
      known
    - include them in `STATUS` control responses when known
    - preserve them in `status.py` task snapshots
    - do not emit activity on every loop tick
  - keep the vocabulary small and uniform. Do not invent pipeline-only status
    verbs when ordinary `activity` plus `waiting_on` explains the behavior
  - update process-title refresh points so the title keeps the durable status
    token and uses the optional detail suffix for derived activity when useful,
    for example `...:running:waiting`
  - keep `weft task list` and the ordinary task snapshot path on the existing
    log-driven reader. Do not add a broad second CLI-only live query loop in
    this task
  - for the pipeline task's own `P{pipeline_tid}.status` snapshot, use the same naming:
    durable `status`, derived `activity`, and optional `waiting_on`
- Tests to write first:
  - `test_prestarted_stage_snapshot_reports_running_status_and_waiting_activity`
  - `test_terminal_snapshot_omits_activity_when_status_is_terminal`
  - `test_pipeline_snapshot_uses_activity_and_waiting_on_fields`
  - `test_activity_change_emits_one_lightweight_log_event`
  - `test_status_snapshot_preserves_activity_from_latest_log_event`
  - `test_process_title_keeps_status_token_and_uses_activity_detail`
- Stop and re-evaluate if:
  - you are about to add a second durable state machine
  - or if the task log payload starts carrying a duplicate full snapshot format
    just for activity reporting
  - or if the activity vocabulary starts expanding into a bespoke workflow
    language
- Done signal:
  - pre-started children no longer look like active work in normal operator
    surfaces
  - durable task state invariants remain unchanged

### 9. Switch `weft run --pipeline` to the first-class runtime

- Outcome:
  the CLI submits a pipeline task instead of running the old stage-by-stage
  loop in-process
- Files to touch:
  - `weft/commands/run.py`
  - `tests/cli/test_cli_pipeline.py`
  - `tests/commands/test_run.py`
- Read first:
  - current `_run_pipeline()` in [`weft/commands/run.py`](../../weft/commands/run.py)
  - new `weft/core/pipelines.py`
  - new `weft/core/spawn_requests.py`
  - [`weft/commands/_result_wait.py`](../../weft/commands/_result_wait.py)
- Required changes:
  - keep the current CLI UX:
    - `weft run --pipeline NAME|PATH`
    - `--input` versus piped stdin validation
  - add `--no-wait` support for pipelines
  - when waiting:
    - submit the internal pipeline task
    - wait on the pipeline task's own outbox and task log via the existing
      result waiter
  - when not waiting:
    - submit the internal pipeline task
    - print the pipeline TID
  - keep the old sequential helper around only until the new path is green
  - remove the old sequential data-copy loop before the change is considered done
- Tests to write first:
  - update existing pipeline CLI tests to cover the first-class runtime
  - add:
    - `test_pipeline_run_no_wait_returns_pipeline_tid`
    - `test_pipeline_wait_returns_final_output_from_pipeline_outbox`
    - `test_pipeline_without_input_does_not_inject_work_envelope_start`
    - `test_pipeline_stage_failure_returns_nonzero_and_names_failing_stage`
- Stop and re-evaluate if:
  - the CLI starts doing stage-by-stage orchestration again
  - or if the runtime code starts depending on CLI-only helpers
- Done signal:
  - `weft run --pipeline ...` submits exactly one pipeline task
  - later stages are no longer launched from the CLI loop

### 10. Prove `result`, `stop`, and `kill` on pipeline TIDs

- Outcome:
  a pipeline run is usable through the existing task verbs without a second
  command family
- Files to touch:
  - `weft/commands/result.py`
  - `weft/commands/tasks.py`
  - `tests/commands/test_result.py`
  - `tests/commands/test_task_commands.py`
- Read first:
  - [`weft/commands/result.py`](../../weft/commands/result.py)
  - [`weft/commands/tasks.py`](../../weft/commands/tasks.py)
  - new pipeline runtime tests
- Required changes:
  - verify whether `weft result <pipeline_tid>` already works once the pipeline
    task logs its custom IO queue names
  - if not, fix the smallest real cause in the result reader
  - keep the result reader generic. Do not add a special-case "pipeline result"
    command path
  - verify whether `weft task stop` and `weft task kill` already work once the
    pipeline task logs its custom control queue names
  - if changes are needed, keep them generic and task-shaped
- Tests to write first:
  - `test_result_reads_pipeline_outbox_by_pipeline_tid`
  - `test_task_stop_stops_pipeline_run`
  - `test_task_kill_kills_pipeline_run`
- Stop and re-evaluate if:
  - a new command family appears
  - or if `result.py` starts parsing pipeline internals instead of reading the
    task's own outbox/control/log surfaces
- Done signal:
  - `result`, `stop`, and `kill` all work against pipeline TIDs

### 11. Add a dedicated `task status` reader for pipeline snapshots

- Outcome:
  `weft task status <pipeline-tid>` can show the pipeline's own live snapshot
  instead of only a flat task-log reconstruction
- Files to touch:
  - `weft/commands/status.py`
  - `weft/commands/tasks.py`
  - possibly new `weft/commands/_pipeline_status.py`
  - `tests/commands/test_status.py`
- Read first:
  - [`docs/specifications/12-Pipeline_Composition_and_UX.md`](../specifications/12-Pipeline_Composition_and_UX.md)
    [PL-5.2], [PL-5.3]
  - [`weft/commands/status.py`](../../weft/commands/status.py)
  - [`weft/commands/tasks.py`](../../weft/commands/tasks.py)
- Required changes:
  - add a helper that peeks the latest `type = "pipeline_status"` payload from
    the pipeline task's `P{pipeline_tid}.status` queue
  - do not consume the queue to answer status
  - use generator-based reads, not fixed `peek_many(limit=...)` shortcuts
  - keep the source order explicit:
    - ordinary tasks stay on the ordinary log-driven snapshot path
    - pipeline TIDs consult the latest `pipeline_status` snapshot first when
      present
    - missing pipeline snapshots fall back to the ordinary log-driven snapshot
  - when a pipeline snapshot exists:
    - show it as the primary unit-level explanation
    - keep the ordinary task snapshot as supporting information
  - when no pipeline snapshot exists:
    - fall back to the current log-based task status path
  - do **not** rewrite `weft status` or `weft task list` in this slice
- Tests to write first:
  - `test_task_status_reads_pipeline_snapshot_when_available`
  - `test_task_status_falls_back_to_log_snapshot_when_pipeline_snapshot_missing`
- Stop and re-evaluate if:
  - you start rewriting the whole status subsystem
  - or if `task status` begins consuming control messages just to render status
- Done signal:
  - `weft task status <pipeline-tid>` explains stages and edges without
    replaying the entire task log

### 12. Update docs, traceability, and remove the Phase 0 runtime path

- Outcome:
  the code, spec, README, and plan backlinks all describe the same shipped
  behavior
- Files to touch:
  - `README.md`
  - `docs/specifications/01-Core_Components.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/specifications/12-Pipeline_Composition_and_UX.md`
  - `docs/lessons.md` if implementation teaches a durable reusable lesson
  - `weft/commands/run.py` to remove dead Phase 0 code
- Read first:
  - the touched specs above
  - this plan
  - the final implementation diff
- Required changes:
  - update implementation mappings from "current Phase 0" to the new runtime
  - keep phase boundaries honest for anything still deferred:
    - autostart pipelines
    - richer `weft status` and `weft task list`
    - automatic same-TID replay
    - `tee` and richer graph features
  - add a backlink from the pipeline spec to this implementation plan
  - remove the dead sequential `_run_pipeline()` loop once the new runtime is
    green
- Tests to run:
  - targeted pipeline tests again after dead-code removal
- Stop and re-evaluate if:
  - documentation starts promising deferred behavior as shipped
- Done signal:
  - specs, README, and code all agree on what pipelines do now and what they
    still do not do

## Follow-On Slices After This Plan

These are real follow-ons, but they do **not** belong in the first merge.

### A. Manager autostart support for pipeline targets

- Files likely to touch:
  - `weft/core/manager.py`
  - `weft/core/pipelines.py`
  - `tests/core/test_manager.py`
- Approach:
  - remove the current "pipeline target not yet supported" warning path
  - route autostart pipeline targets through the same internal pipeline-task
    builder and shared spawn helper used by the CLI

### B. `weft status` and `weft task list` pipeline summaries

- Files likely to touch:
  - `weft/commands/status.py`
  - `weft/commands/tasks.py`
  - `tests/commands/test_status.py`
- Approach:
  - show `role = pipeline`
  - expose current pipeline activity summary without replaying every child log for
    the common path

### C. Automatic same-TID replay / respawn

- Do **not** slip this into the first runtime merge.
- This requires a separate plan because it changes manager supervision
  semantics, child restart rules, and rollback assumptions.

## Verification Gates

Run these as the implementation progresses. Do not wait until the very end.

### Per-slice gates

1. Validation/compiler slice:
   - `uv run pytest tests/core/test_pipelines.py -q`

2. Shared spawn and manager launch slice:
   - `uv run pytest tests/commands/test_run.py -q`
   - `uv run pytest tests/core/test_manager.py -q`

3. Runtime task slice:
   - `uv run pytest tests/tasks/test_pipeline_runtime.py -q`

4. CLI and task-surface slice:
   - `uv run pytest tests/cli/test_cli_pipeline.py -q`
   - `uv run pytest tests/commands/test_result.py -q`
   - `uv run pytest tests/commands/test_task_commands.py -q`
   - `uv run pytest tests/commands/test_status.py -q`

### Final gates

- `uv run pytest tests/core/test_pipelines.py tests/core/test_manager.py tests/tasks/test_pipeline_runtime.py tests/tasks/test_task_execution.py tests/cli/test_cli_pipeline.py tests/commands/test_run.py tests/commands/test_result.py tests/commands/test_task_commands.py tests/commands/test_status.py -q`
- `uv run ruff check weft tests`
- `uv run mypy weft`
- `git diff --check`

If time and environment permit, run the broader default suite after the
targeted gates are green:

- `uv run pytest`

## Review Gates

Before the implementation is considered ready:

- the old sequential pipeline loop must be gone
- no runtime code may import from `weft/commands/*`
- no new public `TaskSpec.spec.type` may exist for pipelines or edges
- no new workflow verb family may exist
- no mock-heavy replacement for the real broker/process path may have been
  introduced
- pipeline public queues must be visible in logged TaskSpec IO
- child stage and edge TIDs must be stable and inspectable by name
- the spec backlinks must be updated

If any of those fail, the implementation is not done.

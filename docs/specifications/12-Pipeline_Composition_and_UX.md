# Pipeline Composition and UX Specification

## Overview [PL-0]

Weft pipelines are a composition surface for saved tasks. They exist to make
durable task chains feel as simple and legible as Unix pipelines while keeping
Weft's existing strengths: queue-visible state, process isolation, task
controls, and strong observability.

The intended product shape is:

- simple verbs
- layered composition
- Unix-friendly input and output
- explicit observability
- composition over tasks, not a separate workflow platform

The core rule is:

**A pipeline is a task-shaped object whose body is composition.**

That rule now governs the implemented runtime.

_Implementation mapping_: `weft/core/pipelines.py` compiles stored
`PipelineSpec`s into a first-class pipeline task plus generated child stage and
edge tasks. `weft/core/tasks/pipeline.py` implements the pipeline and edge
runtime classes. `weft/commands/run.py`, `weft/commands/result.py`, and
`weft/commands/tasks.py` expose the task-shaped CLI surface.

## UX Principles [PL-0.1]

### 1. Stable verbs

Pipelines must use the same core verbs as other Weft objects:

- `run`
- `status`
- `result`
- `list`
- `task stop`
- `task kill`

Weft should not grow a separate top-level workflow verb family as the primary
UX. The center of gravity remains `weft run --pipeline ...`.

### 2. Layered composition

Simple objects compose into more capable ones:

- queues compose into tasks
- tasks compose into pipelines
- pipelines may later compose into richer topologies

Each layer should keep the same operational feel rather than inventing a new
mental model.

### 3. Unix friendliness

Pipelines should feel close to shell composition:

- one command to run them
- pipe-friendly input and output
- easy to inspect
- explicit wiring when complexity grows

But they must remain durable task boundaries, not pretend to be a raw
byte-stream shell pipeline when they are not.

### 4. Observable, not magical

Pipelines must be understandable through normal Weft surfaces:

- queue-visible inputs and outputs
- `weft.state.pipelines`
- `weft.log.tasks`
- `weft status`
- `weft task status`
- `weft result`
- standard Unix process inspection

Durable replay or orchestration behavior must not become hidden magic.

### 5. Composable, not framework-heavy

Pipelines are composition sugar over tasks and queues. They are not a generic
workflow SDK, a visual workflow builder, or a second orchestration platform
with its own opaque control plane.

## Scope and Non-Goals [PL-0.2]

### In scope

- stored pipeline specs under `.weft/pipelines/`
- sequential composition as the default form
- first-stage input from `--input` or piped stdin
- named bindings and aliases as a human-facing wiring affordance
- generated edge tasks for durable linear handoff and checkpointing
- pre-started waiting stage and edge task processes for ordinary linear runs
- stage-local defaults merged into stored task specs
- pipeline runs becoming first-class task-shaped objects in later phases
- pipeline-owned status reporting for unit-level progress
- task-log and status visibility for pipeline, edge, and stage progress
- manager-launched autostart pipeline targets in a later phase

### Explicit non-goals for the initial pipeline product

- a generic workflow programming platform
- a code-first workflow SDK like a durable function framework
- arbitrary DAGs as the default authoring model
- hidden global service discovery as part of core pipeline authoring
- PTY/TTY terminal emulation across stage boundaries
- automatic live byte-stream piping between concurrently running stages
- distributed multi-host orchestration
- a visual builder as part of the core contract

### Out-of-scope until explicitly specified later

- nested pipelines as stage targets
- `tee` or other fan-out semantics
- `tap` or observability-only branch semantics
- pipeline-wide aggregate resource limits
- pipeline-wide retry policies beyond simple stage/task semantics
- automatic recovery, replay, or orphan adoption of interrupted pipeline runs
- speculative graph features that weaken the simple linear case

## User-Facing Model [PL-1]

### Pipeline identity [PL-1.1]

A pipeline spec is a named saved composition. A pipeline run is the execution of
that composition.

Target state:

- a pipeline run has its own TID
- a pipeline run has its own terminal state
- a pipeline run has its own final result
- a pipeline run has its own `inbox`, `outbox`, `ctrl_in`, `ctrl_out`, and
  public `status` queue
- child stages have their own task TIDs
- generated edge tasks have their own task TIDs
- stage runs remain ordinary tasks

### Pipeline verbs [PL-1.2]

The intended user surface is:

```bash
weft run --pipeline etl-job
weft run --pipeline etl-job --no-wait
weft status
weft task status <pipeline-tid>
weft result <pipeline-tid>
weft task stop <pipeline-tid>
weft task kill <pipeline-tid>
```

The important UX rule is not the exact flag order. It is that users do not
need to learn a new command family once the object becomes a pipeline.

Current behavior:

- `weft run --pipeline ... --wait` prints the final pipeline output on success
- `weft run --pipeline ... --no-wait` returns the pipeline TID
- pipeline-level `result`, `task status`, `task stop`, and `task kill` all act
  on that pipeline TID

### Pipeline results [PL-1.3]

By default:

- pipeline input enters the pipeline's own `inbox`
- generated entry and inter-stage edge tasks move payloads onto the next stage's
  handoff path
- the pipeline result is the final stage's final result
- the final stage result leaves through a generated exit edge into the
  pipeline's own `outbox`

This is intentionally simple. It matches shell expectations while staying
honest that stages are durable task boundaries.

For public result assembly, "final result" means the same caller-facing value
that `weft result` would return for that stage task:

- no public values -> `null`
- one public value -> that value
- multiple public values -> ordered list of values
- streamed chunk envelopes -> reassembled into one final string value before the
  rules above are applied

This keeps pipeline chaining aligned with ordinary task result semantics instead
of inventing a second aggregation rule.

For the initial first-class pipeline slice, successful pipeline-compatible
stage tasks must emit exactly one handoff payload on their public outbox. That
payload may be:

- an ordinary serialized value
- JSON `null`
- one spill/reference envelope for large output

Interactive and streaming stage tasks are rejected in this slice, so generated
edges do not need to aggregate multi-message output or reason about source
closure.

### Input semantics [PL-1.4]

- `--input` provides explicit initial input for the pipeline run
- piped stdin may provide initial input when `--input` is absent
- providing both `--input` and piped stdin is a validation error
- stage-local `defaults.input` overrides the inherited input for that stage

Pipeline input is stage input, not a live stream. If a user needs true stream
plumbing, they should use direct queue wiring or an external Unix pipeline.

### Pipeline status model [PL-1.5]

The pipeline run is the primary unit-level status surface.

Queue-plane mental model:

- pipeline `inbox` / `outbox` are the data plane, analogous to `stdin` /
  `stdout`
- pipeline `ctrl_in` / `ctrl_out` are the public control plane, closest to a
  structured mix of signals and `stderr`
- `P{pipeline_tid}.status` is the public status plane for live pipeline
  snapshots
- `P{pipeline_tid}.events` is the private owner-coordination plane between the
  pipeline task and its child stage/edge tasks
- `weft.log.tasks` is the durable audit plane

This is why pipeline child tasks do **not** report to `ctrl_out`. `ctrl_out`
is public and observer-facing for control replies. Child-to-owner coordination
belongs on the private events queue. Live pipeline snapshots belong on the
dedicated `status` queue.

Target state:

- pipeline progress is published by the pipeline task itself
- durable task `status` remains the canonical lifecycle state
- user-facing status may also expose a derived `activity` plus optional
  `waiting_on` so pre-started watchers do not look like active work merely
  because their task state is still `running`
- `ctrl_out` carries control replies for the pipeline run as a unit
- `P{pipeline_tid}.status` carries retained `pipeline_status` snapshots for the
  pipeline run as a unit
- `weft.state.pipelines` carries the active pipeline registry used to discover
  owned children, bindings, and recovery scope
- `weft.log.tasks` remains the audit trail, not the primary live status path
- child stage and edge tasks remain visible as ordinary tasks for deeper
  inspection

This matters because pipelines should not require callers to scrape every child
task queue or reconstruct live state by polling `weft.log.tasks`.

Rationale:

- Unix pipelines keep data on `stdout` and let progress/chatter escape on
  `stderr`
- richer systems such as PowerShell separate data output from progress and
  other non-data streams
- service supervisors often use a dedicated status side channel instead of
  asking observers to reconstruct state from logs alone

Weft follows the same shape. `outbox` stays pure data. `ctrl_out` stays the
control reply surface. Pipelines get one additional public observability
surface, `P{pipeline_tid}.status`, because orchestration is a higher-order
object and retained public status is clearer than asking callers to peek or
drain `ctrl_out`. `weft.log.tasks` remains the durable audit trail underneath
it.

## Pipeline Spec Shape [PL-2]

### Storage and resolution [PL-2.1]

Pipeline specs live under `.weft/pipelines/` and are run through:

```bash
weft run --pipeline NAME
weft run --pipeline ./pipeline.json
```

The CLI may resolve a pipeline by saved name or explicit path, using the same
general `NAME|PATH` rules as other saved specs.

### Minimal shape [PL-2.2]

The baseline pipeline spec is:

```json
{
  "name": "etl-job",
  "stages": [
    { "name": "extract", "task": "extract-data" },
    {
      "name": "transform",
      "task": "clean-data",
      "defaults": {
        "keyword_args": { "mode": "strict" }
      }
    },
    { "name": "load", "task": "load-data" }
  ]
}
```

This minimal form stays intentionally small. The common case should be "saved
durable pipe," not explicit graph programming.

### Stage rules [PL-2.3]

For the initial product contract:

- `stages` is required and must be a non-empty ordered list
- each stage must be an object
- each stage must have a unique `name`
- each stage must reference a stored task spec by `task`
- stage order is meaningful

Stage names are durable identities, not cosmetic labels. Once a pipeline has
first-class durable replay, changing a stage name is a compatibility break for
that pipeline lineage.

Pipeline topology belongs in `PipelineSpec`, not in `TaskSpec`. Child
`TaskSpec`s remain leaf execution units with concrete queue bindings after
pipeline compilation.

User-authored `TaskSpec.spec.type` remains the normal leaf vocabulary
(`function`, `command`, `agent`). Pipeline orchestrators and generated edge
workers are runtime-owned internal tasks. They may be realized with reserved
internal task classes or reserved internal targets without turning pipeline
composition into a new public `TaskSpec` type family.

### Bindings and aliases [PL-2.4]

Pipelines should expose human-meaningful binding names even though concrete
queue names are machine-owned and unique.

The contract is:

- stage ports remain simple names like `inbox` and `outbox`
- logical linear edges are named from the pipeline structure
- concrete queue names are unique and owned by the pipeline run
- the mapping from logical names to concrete queues is inspectable

For the simple linear form, Weft may derive default logical edge names from the
pipeline structure, for example:

- `pipeline-to-extract`
- `extract-to-transform`
- `transform-to-load`
- `load-to-pipeline`

Those logical names then compile into concrete run-owned queues, for example:

- logical edge `extract-to-transform`
- concrete queue `P1837025672140161024.extract-to-transform`

Explicit alias names may be added later, but the product must preserve the
human affordance either way. The important rule is that users think and debug
in terms of named ports and edges rather than opaque queue IDs.

### Defaults merging [PL-2.5]

Stage `defaults` may merge into the loaded task spec template before stage
submission. Supported fields are:

- `input`
- `args`
- `keyword_args`
- `env`

`io_overrides` may be supplied for advanced queue routing, but it remains an
expert feature. The common path should not require explicit queue names.

Resource limits remain stage-local in the initial pipeline product. Referenced
task specs keep their own ordinary task `limits`, and the pipeline contract
does not yet add whole-pipeline aggregate limits or inheritance rules.

### Generated internal edges [PL-2.6]

In the first-class runtime, inter-stage transport is owned by generated edge
tasks rather than by the pipeline task copying values stage-to-stage.

The intent is:

- stage tasks do work
- edge tasks move payloads and checkpoint progress
- the pipeline task coordinates and reports unit-level state

This keeps topology, delivery, and orchestration responsibilities separate.

### Deliberate constraints [PL-2.7]

In the initial pipeline contract:

- stage `task` targets stored task specs, not inline TaskSpecs
- stages do not directly target nested pipelines
- graph wiring is not the default authoring form
- stage output routing is explicit or linear, never inferred from ambient state
- the runtime may generate edge tasks and concrete bindings, but those remain an
  implementation of the pipeline contract, not extra user-authored stages
- pipeline-compatible stages must not be interactive
- pipeline-compatible stages must not use streaming output
- successful pipeline-compatible stages must emit exactly one handoff payload

_Implementation mapping_: `weft/core/pipelines.py` (`_validate_stage_template`
enforces stage compatibility and rejects pipeline or internal edge targets;
`compile_linear_pipeline` realizes concrete stage and edge TaskSpecs).

## Runtime Model [PL-3]

### Historical note: former Phase 0 runner [PL-3.1]

Earlier drafts of this spec described a CLI-owned sequential runner. That path
has been replaced by the first-class runtime described below. The historical
behavior matters only as migration context.

### Phase 1: First-class linear pipeline run [PL-3.2]

The first major runtime upgrade is a first-class linear pipeline orchestrator.

Requirements:

- the pipeline run has its own TID
- the pipeline run runs on the canonical task spine
- the pipeline run owns public `inbox`, `outbox`, `ctrl_in`, `ctrl_out`, and
  `status`
- the pipeline run owns a private internal report path for child owner events
  and edge check-ins
- stages are spawned as ordinary child tasks
- generated edge tasks are spawned as ordinary child tasks
- stage and edge child tasks may be started at pipeline startup and remain idle
  while waiting for work
- child spawn identity must be stable enough that later replay can be
  idempotent
- successful pipeline results must remain retrievable through standard Weft
  surfaces after clean completion

The pipeline orchestrator is still a Weft task. This avoids introducing a
second durable path.

Pipeline startup in this phase should:

1. resolve stage targets from the human pipeline spec
2. resolve or derive logical edge names and concrete queue bindings
3. materialize child `TaskSpec`s for stages and generated edges
4. start those child task processes in an idle waiting state
5. accept input through the pipeline `inbox`
6. let the entry edge move the initial payload to stage 1

This startup shape is acceptable because task processes are queue watchers. The
monitored target subprocess for a stage is not launched until work is actually
reserved and executed.

The UX requirement is that pre-started waiting children remain legible. A
pipeline-compatible status surface must therefore distinguish:

- durable task `status`, which may still be `running`
- derived live `activity`, which may honestly be `waiting`
- optional `waiting_on`, which explains the queue or child surface currently
  blocking progress

`activity` is live-only. Terminal objects should normally rely on `status`
alone and omit `activity` and `waiting_on`.

The pipeline task should consume child owner events and edge check-ins through
a private pipeline-owned event or report queue. Pipeline-owned children may receive
reserved owner metadata at compile time so they can emit compact terminal
events to that queue without extending the public `TaskSpec` model. That
private path should still be an ordinary SimpleBroker queue with a
pipeline-owned private name; it should not become a second hidden transport.
The pipeline should not treat public `ctrl_in` as the normal child-to-parent
report path.
Public `ctrl_in` remains command ingress. Public `ctrl_out` remains the
pipeline's public control-reply surface. Public `P{pipeline_tid}.status`
remains the pipeline's live status and terminal-report surface.

### Phase 2: Pipeline-native status and autostart [PL-3.3]

After the first-class pipeline run exists:

- `weft status` and `weft task list` should surface pipeline runs cleanly
- `weft task status <pipeline-tid>` should show stage progress and child TIDs
- `weft result <pipeline-tid>` should return the final pipeline result
- autostart manifests may target pipelines using the existing target shape

Pipeline autostart should not require a separate manifest schema.
Before this phase, manager autostart of pipeline targets should remain an
explicitly unsupported path that logs a clear warning rather than silently
pretending to launch them.

### Phase 3: Optional richer topology [PL-3.4]

Non-linear graph forms may be added later, but only after the linear chassis is
stable and legible. If added, they must remain explicit and must not disturb the
simple sequential `stages` model.

If Weft later adds richer edge operators, they should stay distinct:

- `move`: linear handoff with checkpointing
- `tee`: explicit fan-out
- `tap`: observability side channel

The first-class linear runtime should only require `move`.

## Failure, Control, and Replay Semantics [PL-4]

### Edge checkpoint contract [PL-4.1]

An edge check-in must mean exactly this:

- the edge durably moved one payload onto the handoff path for the downstream
  stage
- the downstream stage may not have claimed it yet

An edge check-in must not claim more than it knows. In particular, it does not
mean the downstream stage has already reserved the payload or started work. For
stage-output edges, upstream stage completion should come from the separate
stage terminal owner event, not be inferred from the edge alone.

This conservative contract keeps recovery and debugging legible.

Example edge check-in payload:

```json
{
  "type": "edge_checkpoint",
  "pipeline_tid": "1837025672140161024",
  "edge_name": "extract-to-transform",
  "edge_tid": "1837025672140161031",
  "downstream_stage": "transform",
  "downstream_tid": "1837025672140161026",
  "message_id": "1837025672140161029",
  "checkpoint": "queued_for_downstream",
  "queue": "P1837025672140161024.extract-to-transform",
  "timestamp": "1837025672140161032"
}
```

The exact field names may evolve, but the payload must let an operator answer:

- which edge checked in
- which downstream stage is expected to claim it
- which concrete queue currently owns the handoff
- which message instance was moved

Pipeline-owned child tasks may also emit compact owner events on the same
private pipeline events queue. For stage tasks, the important cases are
terminal success and terminal failure. For edge tasks, the important case is
terminal failure, because success is already represented by `edge_checkpoint`.

Example stage terminal owner event:

```json
{
  "type": "stage_terminal",
  "pipeline_tid": "1837025672140161024",
  "stage_name": "extract",
  "child_tid": "1837025672140161025",
  "status": "completed",
  "timestamp": "1837025672140161030"
}
```

These owner events should stay compact. They are not a second full task log.

### Fail-fast behavior [PL-4.2]

Default rule:

- if a stage fails, the pipeline run fails
- if an edge task fails, the pipeline run fails

This is intentionally conservative. It keeps the contract legible and avoids
promising hidden retry behavior around side effects.

The pipeline should surface child failure as its own failure, including:

- failing child name
- failing child TID
- whether the failure came from a stage or an edge
- the last successful checkpoint

After failure, the pipeline should stop admitting new work and should send
`STOP` to waiting children.

### Stage retries and recovery [PL-4.3]

Stage-local retry behavior belongs to the stage task itself. The initial
pipeline contract does not add a second retry vocabulary on top of ordinary
tasks.

Pipeline-wide retry policies are out of scope until explicitly specified.

Reserved-queue semantics remain the recovery primitive for both stages and
edges. The baseline design does **not** require edge move plus progress report
to be one atomic broker primitive. Instead, the contract is:

- the edge should move before it reports the checkpoint
- later recovery work may reconcile queue state with the latest emitted
  checkpoint
- the system must remain explainable after interruption, even if the latest
  checkpoint lags the true queue state

If and when recovery is added later, the relevant inspection surfaces are
expected to be:

- the active record in `weft.state.pipelines`
- the edge task's `reserved` queue
- the downstream stage inbox
- the downstream stage reserved queue
- the downstream stage outbox when the upstream stage may have already
  completed
- live child liveness from normal runtime surfaces

This is acceptable as long as the spec stays honest that recovery is
reconciliation, not blind trust in the latest status event.

Recovery itself is out of scope for the initial first-class pipeline slice.
This slice only needs to leave enough durable breadcrumbs that a later recovery
feature or operator can reason about interruption without hidden state.

When recovery is later implemented, it should bias toward under-claiming
progress. After a crash, it is better for a recovered snapshot to say "last
checkpoint uncertain" than to overstate what completed.

### Stop and kill [PL-4.4]

Target state:

- stopping a pipeline should stop the active stage task and mark the pipeline
  terminal according to the normal task-control rules
- killing a pipeline should kill the active stage task and terminate the
  pipeline run
- already-completed stages remain completed

Control should stay visible through normal task control queues and task-log
events.

Baseline fail-fast propagation:

1. A stage or edge child reports failure.
2. The pipeline task records the failure in its own state and publishes a
   failing status snapshot on `P{pipeline_tid}.status`.
3. The pipeline task stops admitting new work onto later edges.
4. The pipeline task sends `STOP` to waiting stage and edge children.
5. Active children apply their normal reserved-queue semantics before
   acknowledging or terminating.
6. The pipeline task aggregates the stop outcome and transitions to its own
   terminal failed state.

For the initial linear contract, this is intentionally simple: one child
failure fails the pipeline unit. Later policies such as retry or continue must
be explicit opt-ins, not hidden defaults.

### Replay [PL-4.5]

When first-class durable pipelines land:

- replay must not double-spawn a completed stage or completed edge
- replay must not double-commit a completed stage result
- stage identity for replay is at least `(pipeline-run identity, stage name)`
- edge identity for replay is at least `(pipeline-run identity, edge name)`

The exact persistence shape is an implementation detail, but it must remain
compatible with Weft's queue-first observability model. Pipelines must not
quietly introduce a second opaque workflow database.

Replay and automatic recovery are not part of the initial first-class pipeline
slice. This section describes constraints the first slice must preserve so a
later recovery feature can be added without changing the core queue model. A
separate user-facing `--resume` flag remains optional future UX.

## Observability [PL-5]

### Core observability contract [PL-5.1]

A user should be able to answer:

- what pipeline is running?
- which stage is active?
- which edge most recently checked in?
- which stages have completed?
- what child task TID corresponds to this stage?
- why is the pipeline waiting or failing?

without leaving normal Weft tools.

### Required surfaces [PL-5.2]

Target surfaces:

- the pipeline task's own `P{pipeline_tid}.status` snapshots
- the pipeline task's own `ctrl_out` control replies
- the pipeline task's own `outbox` for the final result
- `weft.state.pipelines`
- `weft.log.tasks`
- `weft status`
- `weft task list`
- `weft task status`
- `weft result`
- process titles and PIDs for the pipeline orchestrator and child tasks

### Snapshot shape [PL-5.3]

Pipeline snapshots should come from the pipeline task itself on
`P{pipeline_tid}.status`. They should not require callers to reconstruct live
state by replaying the whole task log.

The exact JSON shape may evolve, but snapshots should cover at least:

- pipeline TID and durable `status`
- optional live-only pipeline `activity` and optional `waiting_on`
- current stage and current edge, if any
- per-stage durable status and child TIDs
- per-edge durable status and child TIDs
- a per-child derived `activity` and optional `waiting_on` when that is useful
  for explaining a pre-started child task that has not yet reserved work
- last successful checkpoint
- current failure details, if any
- resolved binding names and concrete queues when relevant for debugging

The task process for a pre-started child may already be alive while it is still
waiting on an empty inbox. In that case, the child task's ordinary task state
may remain `running` while the pipeline snapshot exposes the more useful
derived `activity = waiting`.

Example pipeline status snapshot:

```json
{
  "type": "pipeline_status",
  "pipeline_tid": "1837025672140161024",
  "pipeline_name": "etl-job",
  "status": "running",
  "activity": "waiting",
  "current_stage": null,
  "current_edge": null,
  "last_checkpoint": {
    "edge_name": "extract-to-transform",
    "checkpoint": "queued_for_downstream",
    "message_id": "1837025672140161029",
    "queue": "P1837025672140161024.extract-to-transform",
    "timestamp": "1837025672140161032"
  },
  "queues": {
    "inbox": "P1837025672140161024.inbox",
    "outbox": "P1837025672140161024.outbox",
    "ctrl_in": "P1837025672140161024.ctrl_in",
    "ctrl_out": "P1837025672140161024.ctrl_out",
    "status": "P1837025672140161024.status",
    "events": "P1837025672140161024.events"
  },
  "stages": [
    {
      "name": "extract",
      "child_tid": "1837025672140161025",
      "status": "completed"
    },
    {
      "name": "transform",
      "child_tid": "1837025672140161026",
      "status": "running",
      "activity": "waiting",
      "waiting_on": "P1837025672140161024.extract-to-transform"
    },
    {
      "name": "load",
      "child_tid": "1837025672140161027",
      "status": "created",
      "activity": "queued",
      "waiting_on": "weft.spawn.requests"
    }
  ],
  "edges": [
    {
      "name": "pipeline-to-extract",
      "child_tid": "1837025672140161030",
      "status": "completed"
    },
    {
      "name": "extract-to-transform",
      "child_tid": "1837025672140161031",
      "status": "completed"
    },
    {
      "name": "transform-to-load",
      "child_tid": "1837025672140161033",
      "status": "created",
      "activity": "queued",
      "waiting_on": "weft.spawn.requests"
    }
  ],
  "bindings": [
    {
      "edge_name": "extract-to-transform",
      "queue": "P1837025672140161024.extract-to-transform"
    }
  ],
  "failure": null,
  "timestamp": "1837025672140161034"
}
```

The exact shape may change, but the snapshot must remain compact enough for
`weft task status` and rich enough to explain where the pipeline is waiting.

_Implementation mapping_: `weft/core/tasks/pipeline.py`
(`PipelineTask._build_initial_snapshot`, `_handle_stage_started`,
`_handle_stage_terminal`, `_handle_edge_started`, `_handle_edge_checkpoint`,
`_publish_pipeline_snapshot`) produces retained `pipeline_status` messages on
`P{pipeline_tid}.status`. `weft/commands/_task_history.py` resolves the queue
from logged pipeline TaskSpecs, and `weft/commands/tasks.py`
(`_latest_pipeline_status_snapshot`, `_prefer_pipeline_snapshot`,
`_pipeline_task_snapshot`) applies the precedence rule for `weft task status`.

### Active pipeline registry [PL-5.4]

Pipelines should also register themselves in a runtime state queue:

- queue name: `weft.state.pipelines`
- one active record per live pipeline run
- record owned and deleted by the pipeline task

The active record is not the primary live status surface. That remains
`P{pipeline_tid}.status`. The active record exists to bound recovery and inspection. It
should contain enough information to answer:

- which pipeline spec or stored pipeline name this run came from
- which stage and edge child TIDs belong to this pipeline
- which concrete `P{pipeline_tid}.*` queues belong to this pipeline
- which logical edge names map to which concrete queues

The record may also include a compact copy of the latest pipeline activity, but it
must not become a second opaque state machine.

Cleanup rule:

- the pipeline task writes the active record during bootstrap before it starts
  depending on child work
- on a clean terminal path, the pipeline task deletes the active record only
  after publishing its terminal `status` snapshot and after any final public
  pipeline outbox result is durably available
- if the process crashes before cleanup, the stale record is intentional and is
  the breadcrumb used for orphan inspection and later replay work

### Audit events [PL-5.5]

Pipeline runs should emit ordinary task lifecycle events for the pipeline run
itself and additional stage-progress events that make child-stage progress
explicit.

The exact event names may evolve during implementation, but the product
requirement is stable: logs must remain a durable audit trail. They are not the
primary recovery substrate for pipelines.

### Debuggability rule [PL-5.6]

The product should support "rummaging around" in the live system:

- tapping task output
- inspecting edge checkpoint progress
- inspecting child stage TIDs
- understanding current wiring and waits

If a pipeline design cannot be debugged with ordinary Weft status, logs, queue
inspection, and Unix process tools, it is too hidden.

## Product Boundaries [PL-6]

Pipelines in Weft are:

- more durable than shell pipelines
- more observable than ad-hoc scripts
- more Unix-shaped than workflow platforms
- more queue-visible than in-memory orchestration systems

Pipelines in Weft are not:

- a replacement for direct queue wiring
- a replacement for external Unix pipes
- a distributed workflow system
- a code-first orchestration DSL
- a hidden fan-out system where aliases quietly mean `tee`

Aliases and binding names are a human affordance. They help users communicate
and debug the topology. They do not change the underlying delivery semantics.

## Implementation Order [PL-7]

### Ordered scope

1. **Historical Phase 0**: the CLI-owned sequential runner that preceded the
   first-class runtime
2. **Phase 1**: first-class linear pipeline run with its own TID, generated
   edge tasks, pre-started waiting children, fail-fast semantics, and
   pipeline-owned status snapshots
3. **Phase 2**: pipeline-native autostart and stronger status/result/control
   integration across the CLI
4. **Phase 3**: explicit richer topology and edge operators (`tee`, `tap`) only
   if the simple linear case remains easy to use and easy to inspect

### Do not invert the order

Weft should not implement:

- graph authoring first
- a new workflow verb family first
- hidden service wiring first
- visual-builder UX first

before it has a solid, first-class, linear, task-shaped pipeline run.

## Related Plans

- [`docs/plans/pipeline-spec-expansion-plan.md`](../plans/pipeline-spec-expansion-plan.md)
- [`docs/plans/pipeline-first-class-runtime-implementation-plan.md`](../plans/pipeline-first-class-runtime-implementation-plan.md)

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[02-TaskSpec.md](02-TaskSpec.md)** - Task and manifest specification
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Queue and lifecycle flows
- **[10-CLI_Interface.md](10-CLI_Interface.md)** - Command-line surface

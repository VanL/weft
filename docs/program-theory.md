# Weft Program Theory

Status: Active, adopted 2026-11-02 through
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` ([REV-WT-004]);
revised only through a class-5 plan
Owner: Weft product owner
Boundary: The identity of Weft and the rules for design judgment. Not the
exact current behavior. The specifications in `docs/specifications/` give the
exact current behavior.
Verification: Four measures, each named for what it measures. Owner
endorsement: the owner can defend each sentence of [WT-2a], [WT-4], [WT-5],
and [WT-6] as their own, confirmed at adoption and at each [REV-WT-*]
record; endorsement is not a demonstration of possession. Transfer probe:
at each release or class-5 completion, a possession probe with an
owner-written key (the feature to place, the category error to refuse, and
the predicted bug class are fixed before the probe runs), executed by an
agent that did not author the plan and graded against the key; on FAIL,
classify after disposition as document deficiency, key fault, or agent
reasoning, and record a document deficiency only when the evidence supports
it. Transfer log: a review proposal of a [WT-6] alternative that does not
cite the entry's recorded reopening condition is logged against that entry
(see [WT-7]). Theory-versus-code audit table: at each release or class-5
completion, the table that maps each [WT-2a] invariant and [WT-4] principle
to its contract and its evidence in the tree as aligned, drifted (naming
the plan that closes it), or theory revision; unchanged rows carry forward
by reference and touched claims are re-audited. The table measures
document-versus-tree agreement, not possession.
Required action: Read this document before you make a product-scope or
design decision. Obey it, or propose a theory revision as a class-5 change.
Governing process: Theory revisions are class-5 plans with one cross-family
review round (a second only after a block or a scope change), the keyed
possession probe and the theory-versus-code audit refresh at each release or
class-5 completion ([WT-7]), and the Replacement-sync gate in
`bin/check-doc-paths` for promoted plans. SimpleBroker uses `[DOM-16]` in its
operating-model specification for the same purpose.

This document uses direct, active-voice prose. It states causal relationships
explicitly—why a boundary exists, which classical model a mechanism realizes,
and how primitives compose—while avoiding opaque abstraction. The words "must"
and "must not" state requirements. Quotations from the owner keep their
original words.

## Technical names

These words are technical names in this document. They have the meaning
that the Weft specifications give them.

- queue, inbox, outbox, reserved queue, `ctrl_in`, `ctrl_out`, task log
- task, TID, TaskSpec, Manager, Consumer, service task, pipeline, agent
- reactor, wake arbiter, turn, timer, hint, event, source adapter
- owner, custody, lease, session, broker target, backend
- evidence, terminal, liveness, supersession, admission, reap, sentinel
- runtime-state queue (`weft.state.*`), family, frontier, label
- durable, hybrid timestamp, spawn request

## What "program theory" means [WT-0]

Weft uses the meaning of "theory" from Peter Naur, "Programming as Theory
Building" (1985).[^naur] A program theory is not a formal theory and not a
list of requirements. It is the working explanatory model that connects the
problem to the concepts, ownership boundaries, and limits of the program.

A person or agent possesses the theory when they can do four things:

- connect a problem in the world to the shape of the program
- explain and justify why the shape is what it is
- add a new demand and keep the program coherent
- place a proposal in the right owner, and refuse a proposal that does not fit

Repeating this document from memory is not possession.

The friction is the point. A change or review finding that looks locally
optimal in a single file—adding a second queue lane to bypass a backlog,
charging a guessed resource budget per task, clamping a reactor loop with a
fixed polling cap, splitting a cohesive state machine by line count, or using
a queue as an unconsumed relational table—regularly contradicts an earlier
empirical failure, a cross-process ownership boundary, or the program's
conceptual unity. In particular, a frequent source of bad designs is applying
Weft's tools in a blunt, ad-hoc fashion to patch an immediate symptom rather
than starting from the right computer-science model (event-driven reactor
demultiplexing, bounded depth-first task scheduling, measured admission
backpressure, custody handoff, multicast notification) and asking how Weft's
existing queue and process primitives realize that well-studied algorithm.

The owner gave this theory a name on 2026-09-08: "weftness", defined as "the
conceptual core of the program". SimpleBroker has the companion account in
`../simplebroker/docs/program-theory.md` ("simplebrokerness"), and Taut has
`../taut/docs/program-theory.md` ("tautness").

Each documentation surface answers one question:

| Surface | Question it answers |
|---------|---------------------|
| Program theory (this file) | What is the conceptual **ideal** of this system or module? What is its feel/idiom, core composable operations, and **identity invariants** (the choices that shape the entire program)? Why do these concepts, boundaries, non-goals, and discarded paths exist? |
| Specifications (`docs/specifications/`) | What is the exact intended behavior now? |
| Plans (`docs/plans/`) | How are we morphing the code and specs closer to the program theory, and which alternatives were rejected or revised? |
| Code and tests | How is the behavior realized, and what executable evidence fires against each claim? |
| Lessons (`docs/lessons.md`) | What durable rule did a surprise or repeated failure establish? |

The owner stated the rule governing the first two rows on 2026-10-09: "The
specs describe what we have. The theory describes what we are building toward.
We are not trying to reify bad decisions." If a specification contradicts the
theory, the specification is revised through a planned change; the theory does
not bend to ratify an accidental or flawed implementation.

Three directional rules govern how program theory, specs, and plans interact:

- **The program theory is the ideal** — what the software is aiming to be.
- **Specs describe what is currently in the code** — the concrete behavioral
  contract for the current implementation (not an unbuilt wishlist; a spec leads
  the code only across an active plan's spec-promotion slice or when a bug
  violates the contract).
- **Plans morph the code (and the specs) closer to the ideal.**

Four rules to help keep things aligned (`[REV-WT-004]`):

1. **If the specification or code is pulling away from the theory, the
   presumption is that the specs or code are not properly moored in the
   theory.** If the code and the spec disagree, then the presumption is that the
   code has a bug. When a spec clause itself is stale or when experience
   building and operating the code suggests a better path, then use a plan to
   fix the spec itself.
2. **Never mistake a current spec or plan for "what has been decided" and try
   to justify it when it falls short of the theory.** Everything—including the
   theory itself (`[REV-WT-*]`)—can be revised as implementation and experience
   teach us more.
3. **Respecting active contracts during an edit is about change order, not
   permanence.** Do not silently break an active spec clause mid-edit; use a
   plan (and its spec-promotion slice) to move the spec and code toward the
   theory.
4. **Process exists to keep theory and specs aligned while working at agent
   speed.** Any process rule that does not improve orientation, error detection,
   or human understanding should be cut.

[^naur]: Peter Naur, "Programming as Theory Building" (1985); reprinted in
    *Computing: A Human Activity* (1992).

## Purpose and desired feel [WT-1]

Weft is "the durable task substrate for agent systems". It runs commands,
Python callables, pipelines, and agent-backed work that can take milliseconds
or hours and can outlive the shell session or parent process that submitted it.

Weft must feel like a small set of composable Unix tools, not a heavyweight
orchestration platform. An operator can inspect all live state with standard OS
tools (`ps`) and a queue peek. Failures remain visible where they happened. The
system performs no background magic that the caller or context configuration
did not ask for.

"Simple" means a small number of orthogonal concepts and a predictable operational
model at the use surface—not a small number of source lines. A large file is
right when its responsibilities share state and must coordinate across failure
and lifecycle boundaries (`AGENTS.md` §1.1).

## Whole-program mental model [WT-2]

Weft is an operating-system-style task and process scheduler built strictly out
of SimpleBroker queues and OS processes:

1. **Everything is a task (`T{tid}.*`)**:
   A Manager is a task that spawns and supervises other tasks. An agent is a
   task. A pipeline is a task. Even a short-lived CLI requester (`weft status`,
   `PING`, result waiter) mints a TID and receives replies on its own
   `T{tid}.ctrl_in`. The owner stated this invariant on 2026-09-17: "In weft,
   everything is a task" and "There is no code without a task identity."

2. **Queues carry durable facts; processes carry ephemeral hints; consumed queues disappear**:
   If a Weft lifecycle or coordination fact is durable, it is durable because a
   message sits in a SimpleBroker queue. In-memory structures inside a Manager
   or Consumer are performance hints or local process handles—never a second
   lifecycle database. Individual tasks may maintain their own task-internal
   working state to do their job—for example, `TaskMonitor` maintains private
   collation and export cursors in sidecar tables (`MonitorStore`) via
   SimpleBroker's `broker.sidecar()` facility so it can transform `weft.log.tasks`
   into external JSONL logs and prune covered rows—but **Weft never depends on
   task-internal state for its operation**. Crucially, SimpleBroker queues are
   **auto-created on first write and auto-disappear when emptied** (`move_one`
   or `delete`). Weft uses per-task and per-branch queues (`T{tid}.*`,
   `weft.state.labels.<root_tid>.pending.<parent_tid>`) freely because consuming
   messages as work progresses cleans up the queue namespace automatically.
   Treating a SimpleBroker queue as an unconsumed, append-only relational table
   that requires tombstones and manual garbage collection violates the substrate
   model.

3. **Detached process topology bridged by task-owned runtime state (`weft.state.tasks.<tid>`)**:
   When task $A$ submits child task $B$, $A$ does not `fork` or `exec` $B$; $A$
   enqueues a spawn request in `weft.spawn.requests` and the Manager launches
   $B$ in an isolated `spawn`-context process. Furthermore, ordinary user tasks
   are launched detached from the Manager's lifetime ([MA-3]) so running
   tasks survive Manager drain, idle timeout, crash, or supersession. Because
   neither the submitting parent nor a replacement Manager holds the child in
   its OS `ppid` tree or its in-memory child table, `weft.state.tasks.<tid>`
   is the authoritative bridge between scoped OS process identity
   (`(pid, create_time)`) and logical task lineage (`root_tid`, `submitter_tid`,
   `ancestry_tids`, `scheduling_class`). `LivenessMonitor` is the sole component
   that deletes `weft.state.tasks.<tid>` once the OS process is proven dead.

4. **Each task is a single-arbiter event reactor**:
   One wake arbiter—SimpleBroker's `PollingStrategy`—waits on a small, closed
   set of inputs: backend queue activity (`PRAGMA data_version` on SQLite;
   `LISTEN`/`NOTIFY` on PostgreSQL), local thread/adapter completions via
   `notify_activity()`, and explicit clock or absence deadlines via
   `next_wait_timeout()`. Blocking OS waits (such as `multiprocessing.connection.wait`
   on child process sentinels) run in dedicated source-adapter threads that
   publish one immutable completion fact and wake the arbiter. If no input
   arrives and no timer is due, a quiet task runs zero turns.

   The same reactor law governs worker-lane and subprocess supervision loops
   that run inside a task's worker lane: an inner supervisor waiting on child
   processes, pipes, or session channels must wait on OS readiness (readiness
   demultiplexing on POSIX; overlapped completion waits on Windows) with the
   next scheduled supervision deadline (execution
   timeout or resource-monitor sample interval) as a protective ceiling—never
   by polling non-blocking descriptors on a fixed timer floor.

5. **Classical CS algorithms over existing primitives**:
   Weft solves scheduling, overload, and coordination problems by mapping them
   to well-understood computer science algorithms over its existing primitives:
   - *Overload and resource protection* is **measured admission control** on
     process creation (`available = ceiling - reserve - measured_usage`) with
     non-blocking startup feedback, while queue-first submission remains
     unbounded and free of process/connection charges.
   - *Nested parent/child scheduling under finite capacity* is **work-conserving
     depth-first preference with work sharing** (FIFO across independent
     families, round-robin across active ancestry paths, LIFO descent along a
     tip's children, and oldest-sibling sharing),
     backed by consume-on-dispatch per-parent pending queues.
   - *Crash-safe launch handoff* is **two-phase reservation custody** between
     `T{manager}.reserved` and `weft.state.tasks.<manager_tid>` until
     task-owned `weft.state.tasks.<tid>` evidence or whole-process settlement
     is observed.
   - *Result and completion waiting* is **observer-addressed multicast** (one
     atomic broadcast to `weft.log.tasks` and `T{tid}.ctrl_out`) rather than
     $O(W \cdot E)$ shared-log polling.

6. **Explicit layer ownership**:
   - **SimpleBroker** owns queue semantics, hybrid-timestamp message IDs,
     atomic reservation moves, delivery claims, and connection attribution.
   - **The backend** (SQLite, PostgreSQL) owns storage durability and
     concurrency primitives.
   - **The Manager** owns admission, frontier selection, task launch, and
     direct child supervision.
   - **Each Task** owns its `inbox`, `reserved`, `outbox`, `ctrl_in`,
     `ctrl_out`, `weft.state.tasks.<tid>` publication, and any task-internal
     working state private to that task.
   - **The operating system** reports process exit through kernel sentinels and
     scoped `(pid, create_time)` identity, never via application heartbeats.
   - **Higher-level intelligence** sits above Weft. Weft runs bounded agent
     work as ordinary tasks through its core agent runtime; it does not plan
     or orchestrate agents, and it does not own provider accounts, provider
     health, or approval policy ([AR-0.0], [AR-8]).

## Identity invariants [WT-2a]

These are the properties that make a program Weft. Each one would still
measure a replacement implementation written from scratch: if a rewrite
broke one, the rewrite would be a different system, however well it passed
today's tests. This section states each invariant once, at that altitude.
The exact contract text, its edge cases, and its implementation mapping live
in `07-System_Invariants.md` and the other specifications named in brackets.
This section points at them and does not restate them. `CLAUDE.md` §3 and
`AGENTS.md` §3 point here for identity and to spec 07 for the remaining
contract invariants. A proposal that breaks an invariant here is a class-5
theory revision, not a spec edit.

- **[WT-2a.1] One task identity, rendered losslessly.** Every unit of work
  has exactly one identity: a SimpleBroker hybrid timestamp minted from the
  broker's timestamp generator, used as the spawn-request message ID for
  spawned work, and carried unchanged for the task's whole life. Every
  rendering of it, in a queue name, a process title, a log line, or a CLI
  argument, decodes back to the same TID ([IMMUT.3], [SB-0.2]). (Owner
  decision, 2026-11-02; the ten-character form in [OBS.5] is retired by the
  TID display-encoding plan.)
- **[WT-2a.2] Queues are the only durable truth.** A Weft lifecycle or
  coordination fact is durable only as a SimpleBroker queue row; process
  memory holds hints. A task's private working state exists only to do
  that task's own job ([ALT-WT-020]), and no Weft execution, control,
  status, or result path depends on it. [OBS.2] states that Weft keeps no
  separate lifecycle store, alongside [OBS.1] ([QUEUE.1]–[QUEUE.6]).
- **[WT-2a.3] Forward-only lifecycle.** A task's lifecycle moves only
  forward, and a terminal state never returns to a non-terminal one
  ([STATE.1], [STATE.2]).
- **[WT-2a.4] Frozen execution definition.** What a task runs (`spec`) and
  how it is wired to queues (`io`) are fixed when the TaskSpec is created;
  only runtime state and metadata change afterward ([IMMUT.1], [IMMUT.2],
  [IMMUT.4]).
- **[WT-2a.5] Process isolation.** Every Weft runtime process starts from a
  fresh process image and opens its own broker state; no broker handle,
  connection, or descriptor crosses a process boundary ([IMPL.5], [IMPL.6]).
- **[WT-2a.6] One arbiter per reactor; reactors compose by messages.** Every
  reactor owner has exactly one arbiter that waits on a closed set of inputs
  and commits that owner's broker effects. A reactor may own child reactors.
  Parent and child exchange only immutable controls downward and immutable
  events upward over declared channels, each waking the other's arbiter, and
  no broker handle, waiter, or mutable state crosses an owner boundary. A
  task is the root owner of its reactor tree, and a parent never holds a
  child's private handles or mutable domain state; it reads only the facts
  the child publishes (its events, its queue rows, its state snapshots) and
  the lifecycle evidence the parent itself owns ([IMPL.8], [IMPL.10],
  [IMPL.11]; sibling
  realization: Taut [TAUT-8.5] and [MCP-8] over the shared
  `MultiQueueWatcher` copy). (Owner, 2026-11-02, after reviewing Taut's
  reactor over workspace reactors.)
- **[WT-2a.7] Exactly once per reservation.** A work item executes exactly
  once per successful reservation path; an unsettled reserved row persists
  independently of the process that took it until it is acknowledged or
  explicitly dispositioned, and its disposition when execution does not
  complete is the task's declared policy, keep or clear, never a guess
  ([EXEC.1], [QUEUE.4] to [QUEUE.6]; crash residue awaits explicit recovery,
  [MF-2]).
- **[WT-2a.8] SimpleBroker owns queue mechanics.** Message identity,
  ordering, atomic moves, delivery claims, and storage transactions belong
  to SimpleBroker; a queue-level need is met in SimpleBroker, never
  reimplemented in Weft, and SimpleBroker never depends on Weft ([WT-5],
  [SB-0.1], [SB-0.4]).
- **[WT-2a.9] One current contract.** Each Weft contract has exactly one
  current shape; a change updates every Weft-owned producer and consumer
  together, with at most a bounded forward migration for durable old data
  ([ALT-WT-012]; `00-Overview_and_Architecture.md`, Single current
  contract).
- **[WT-2a.10] A Unix citizen.** Weft behaves as a Unix tool: a command
  runs as an argv list, never through a shell; piped stdin reaches the task
  as data; and the exit code carries the outcome, with the code mapping
  fixed in [IMPL.1] ([CLI-1.1.1], [TS-1]). The owner stated the shell rule
  on 2026-11-02: "Weft does not support shell=True. The only way someone
  could get a shell is if they construct a task with a command line that
  creates a shell."
- **[WT-2a.11] One operator trust domain.** Weft trusts whatever the
  operating system let the current user place or reach; the operating
  system, filesystem permissions, and database grants decide who can. Weft
  validates shape and recognizes its own artifacts for correctness and
  portability, never as a trust boundary, and adds no in-band authentication
  or sanitizer that would imply one ([WT-5], [ALT-WT-014]; owner decision,
  2026-11-02).
- **[WT-2a.12] Everything is a task, and every protocol is a message.** Any
  Weft code that exchanges facts with a task does so as a task: it holds a
  TID from SimpleBroker's hybrid-timestamp generator and receives replies on
  its own `T{tid}.ctrl_in`. A protocol is an ordinary message on an ordinary
  queue, pushed to a queue its recipient already watches; no caller has a
  privileged side-channel ([MF-3]). (Owner, 2026-09-17: "In weft, everything
  is a task" and "There is no code without a task identity.")
- **[WT-2a.13] Independent lifetimes; death needs proof.** A shared Manager,
  each task it launches, and each waiting client have independent
  lifetimes: Manager supersession, idle exit, or crash changes who takes
  future work and never kills or moves running tasks. A system STOP is
  different: it is the Manager's responsibility to stop, suspend, or kill
  active tasks, then its internal services, then itself (owner,
  2026-11-02). Because no lifetime contains
  another, absence is never proof of death: a missing snapshot, an expired
  window, or a failed probe is `unknown`, and task-owned terminal proof
  outranks any supervisor's inference ([MA-1.6a], [MA-3], [MF-5],
  LIVENESS.R3 to LIVENESS.R7; [PY-2]).
- **[WT-2a.14] Submission never blocks.** Every submission, root or child,
  is accepted into the spawn-request queue without waiting for launch
  admission and costs nothing while it waits; only a running process
  consumes capacity ([MF-1]). Readiness work after acceptance, such as
  observing Manager availability or starting a Manager, is separate from
  acceptance and may take time. (Owner, 2026-10-14: "Tasks sitting waiting
  to be spawned do not consume processes. Only live processes do")

## Core concepts and ownership [WT-3]

| Concept | Meaning | Owner | Exact contract owner |
|---------|---------|-------|----------------------|
| Task | One unit of work with a TID, a TaskSpec, and a per-task queue set | The task process | `01-Core_Components.md` [CC-2.1], [CC-2.2.1] |
| TID | A 64-bit SimpleBroker hybrid timestamp; the spawn-request message ID becomes the task's immutable TID | SimpleBroker mints the ID; Weft preserves it | `04-SimpleBroker_Integration.md` [SB-0.2]; `07-System_Invariants.md` IMMUT.3 |
| TaskSpec | Frozen execution specification (`spec`, `io`) plus forward-only runtime state (`state`) | Canonical payload resolution under one owner fixes `spec` and `io`; the task that owns the TaskSpec publishes `state` | `02-TaskSpec.md` [TS-1], [TS-1.4]; IMMUT.1, IMMUT.2 |
| Lifecycle state | Forward-only state machine (`created` → `spawning` → `running` → terminal) | The task that publishes the transition | `07-System_Invariants.md` STATE.1, STATE.2 |
| Manager | A task that classifies requests, enforces measured admission and branch frontiers, reserves and spawns children, and reaps exits | The Manager process | `03-Manager_Architecture.md` [MA-1], [MA-1.8], [MA-3] |
| Branch frontier | A live finite ancestry path retained for child-first scheduling, without a family concurrency permit (LIFO descent, FIFO root entry) | The Manager process (checkpointed in `weft.state.tasks.<manager_tid>`) | `03-Manager_Architecture.md` [MA-1.8]; `2026-10-15-work-conserving-family-scheduling.md` |
| Pending family bucket | Consume-on-dispatch queue `weft.state.labels.<root_tid>.pending.<parent_tid>` indexing accepted child requests by parent; auto-disappears when emptied | The Manager process | `05-Message_Flow_and_State.md` [MF-5]; `2026-10-15-work-conserving-family-scheduling.md` |
| Reactor and wake arbiter | Single wait point (`PollingStrategy`) per task over a closed set of queue, event, and deadline inputs | The task driver thread | [CC-2.2.1]; `07-System_Invariants.md` IMPL.8 to IMPL.11 |
| Source adapter | Dedicated helper thread that owns one blocking OS or worker wait and wakes the reactor with one immutable result | The task that created it | [CC-2.2.1]; `2026-09-17-watcher-reactor-restoration-plan.md` |
| Platform readiness adapter | Narrow OS-specific leaf inside a runner that waits on subprocess pipes or handles using the platform's native readiness primitive behind an opaque supervisor interface | The runner supervision loop | `06-Resource_Management.md` [RM-5.2] (promoted with `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`); `01-Core_Components.md` [CC-2.5], [CC-3.4] |
| Control message | A request row on `T{target}.ctrl_in`; replies are pushed to the requester's own `T{requester}.ctrl_in` (`reply_to`) | The task reading `ctrl_in` | `05-Message_Flow_and_State.md` [MF-3]; `2026-09-18-event-routed-manager-pong-plan.md` |
| Completion fact | One serialized terminal record published atomically to `weft.log.tasks` and `T{tid}.ctrl_out` | The completing task | [MF-3], [MF-5]; `07-System_Invariants.md` OBS.1 |
| Runtime-state queue | `weft.state.*` ephemeral operational state (excluded from dumps, read via peek-latest; `weft.state.tasks.*` deleted only by `LivenessMonitor`) | The publishing task or service; `LivenessMonitor` for task-state deletion | `07-System_Invariants.md` OBS.6, LIVENESS.R3; `05-Message_Flow_and_State.md` [MF-5] |
| Task-internal working state | Private state maintained by an individual task for its own work (such as `TaskMonitor`'s `MonitorStore` sidecar tables for log collation, JSONL export, and retention pruning); never read or depended on by Weft's execution, control, or query paths | The individual task that owns it (`TaskMonitor` for `MonitorStore`) | `07-System_Invariants.md` [OBS.13]; `04-SimpleBroker_Integration.md` [SB-0.4a] |
| Broker session | Process-local lease over shared backend runner/pool resources with single-thread caller cache | The task or command that opened it | `04-SimpleBroker_Integration.md` [SB-0.4]; `01-Core_Components.md` [CC-2.2.1] |
| Admission | Measured gate on process creation (`available = C - R - U`), never on queue-first submission | The Manager | `03-Manager_Architecture.md` [MA-1.8]; `2026-10-15-work-conserving-family-scheduling.md` |
| Evidence | Queue row, scoped `(pid, create_time)` process identity, or runtime handle supporting a liveness or custody claim | One broker-free liveness policy (`weft.liveness`) classifies it; every reader applies that policy rather than its own classifier | [MF-5]; `07-System_Invariants.md` LIVENESS.R3–R7 |
| Runner plugin | A named runtime backend that executes a task's work behind an opaque runtime handle; core sees ordinary runner options and the handle, never the runtime's internals | The runner plugin surface (`weft.ext` contracts, resolution in `weft._runner_plugins`); each plugin owns its runtime-specific execution | `01-Core_Components.md` [CC-3.1], [CC-3.2]; `02-TaskSpec.md` [TS-1.3] |
| Agent runtime and session | Agent-backed work run as an ordinary task: the agent TaskSpec section, the runtime adapters, and a per-task persistent conversation in a dedicated session subprocess; no second durable conversation store | The agent runtime modules (`weft.core.agents`); the owning task's session (`weft.core.tasks.sessions`) for persistent scope | `13-Agent_Runtime.md` [AR-0.0], [AR-5], [AR-6], [AR-8] |
| Service registry | `weft.state.services` runtime records naming the live owner of each Manager and managed service, plus the convergence policy that selects one live owner among duplicates | The Manager (`weft.core.manager_services` for managed-service state, `weft.core.service_convergence` for convergence) | `03-Manager_Architecture.md` [MA-1.6], [MA-1.6a]; `07-System_Invariants.md` MANAGER.3, OBS.15; [ALT-WT-007] |

Concepts that compose from the rows above and need no row of their own:

- **Pipeline** is a task whose stages are ordinary tasks (Task, Control
  message, Completion fact; [PL-1.1]). [WT-5] refuses the DAG/join compiler.
- **Endpoint** is a name record in `weft.state.endpoints` published by the
  task that owns the name (Runtime-state queue, Task; [CC-2.4.1]).
- **Streaming** markers in `weft.state.streaming` are ownership hints over a
  task's own outbox (Runtime-state queue, Task; [CC-2.4]).
- **Heartbeat** is a service task that emits messages to registered
  consumers, never a clock (Task, Reactor and wake arbiter; [MF-3.2],
  [ALT-WT-004]).
- **Retention** is consumption, the single task-state deleter, and
  TaskMonitor's private pruning (Runtime-state queue, Task-internal working
  state; OBS.16, OBS.17, LIVENESS.R3).
- **Interactive** mode is queue-mediated line IO over a runner session
  (Task, Source adapter, Platform readiness adapter; [CLI-1.1.1]).

## Design principles [WT-4]

The "Evidence" column names the owner session or plan in which the principle
was established. The "Contract" column names the governing specification or
engineering rule.

### 4.1 Ontology and Queue Mechanics

| Principle | Design consequence | Evidence | Contract |
|-----------|--------------------|----------|----------|
| Everything is a task | No caller gets a privileged side-channel. Even a short-lived CLI requester mints a TID and receives control replies on `T{tid}.ctrl_in`. | Owner, 2026-09-17 | [MF-3]; `2026-09-18` PONG plan; [WT-2a.12] |
| It is just a message | Protocols are ordinary messages on ordinary queues. Replies are pushed to a queue the requester's reactor already watches—push, never pull-scan. | Owner, 2026-09-17: "this should not be a special case. It is just a message." | [MF-3]; [WT-2a.12] |
| Queues carry facts; processes carry hints | No in-memory lifecycle database. Detached task lineage and process evidence live in `weft.state.tasks.<tid>`; local Manager tables are caches/hints rebuilt from queues. | Owner, 2026-09-23; `2026-10-12` burst plan | OBS.1, OBS.6; [MF-5]; [WT-2a.2] |
| Task-internal state is private; Weft never depends on it | An individual task may maintain its own internal state (`TaskMonitor` uses `broker.sidecar()` tables to collate and export `weft.log.tasks` before pruning), but Weft's execution, control, status, and result paths must not read or depend on a task's internal state. | Owner, 2026-10-14 and 2026-10-15; `2026-10-14-task-monitor-read-separation-and-simplification-plan.md` | [OBS.13], [SB-0.4a] |
| Consume-to-disappear queues, not relational tables | SimpleBroker queues auto-create on write and auto-disappear when emptied. Consume tokens as work is dispatched (`delete`/`move_one`) instead of accumulating unconsumed event logs that require tombstones and manual GC sweeps. | Owner & architectural review, 2026-10-14 | [SB-0.1]; `2026-10-14` family scheduling plan |
| Address a fact to its observers | Terminal completion is broadcast atomically to `weft.log.tasks` and `T{tid}.ctrl_out` so task waiters watch only their target's queue instead of scanning global history in $O(W \cdot E)$. | `2026-10-12` burst plan; owner, 2026-10-09 | [MF-3], [MF-5], OBS.1 |
| Keep an identity you already have | An owner that wrote a row retains the returned ID; a caller that created a TaskSpec derives queue names from its TID rather than scanning logs to rediscover it. | Owner, 2026-10-09: "We don't need to re-discover it from the taskspec that we just created" | [MF-5]; [SB-0.2] |

### 4.2 Classical Algorithms and Resource Control

| Principle | Design consequence | Evidence | Contract |
|-----------|--------------------|----------|----------|
| Classical CS models over blunt ad-hoc fixes | Start from the well-studied CS formulation (reactor demultiplexing, work-conserving DFS/work-sharing, measured admission control, two-phase custody handoff) and realize it with existing primitives rather than inventing lanes, heuristics, or magic budgets. | Owner, 2026-10-14 | [MA-1.8]; `2026-10-14` family scheduling plan |
| Queued work costs nothing; live processes cost | Submission is always queue-first and unblocked. Admission gates process creation using measured pressure (`C - R - U`), never prospective per-task connection debt or predeclared child counts. | Owner, 2026-10-14: "Tasks sitting waiting to be spawned do not consume processes. Only live processes do" | [MF-1]; [MA-1.8]; [WT-2a.14] |
| Use the primitives you have | Compose labels, bounded queues, FIFO/LIFO reads, atomic exact-ID moves, and `weft.state.tasks.*` snapshots before proposing new infrastructure. | Owner, 2026-10-14; 2026-09-17: "We already have the primitives we need in the multiqueue watcher" | [SB-0.1], [SB-0.4] |
| Push a concern down when it belongs lower | Connection attribution belongs in SimpleBroker because SimpleBroker opens connections; admission and scheduling policy belong in Weft. SimpleBroker never depends on Weft. | Owner, 2026-10-06, 2026-10-13, 2026-09-17 | [SB-0.4]; [WT-2a.8] |
| Process concurrency, short transactions | Weft isolates work in OS processes (`spawn` context), not worker threads, and keeps broker transactions short. On PostgreSQL the scarce resource is connection count. | Owner, 2026-09-15 | [SB-0.4]; [WT-2a.5] |
| One owner for each resource | Each `BrokerSession`, queue lease, file descriptor, `weft.state.tasks.*` deleter (`LivenessMonitor`), and worker result has a single explicit owner. | Owner, 2026-09-15 | [SB-0.4]; [CC-2.2.1]; QUEUE.7, QUEUE.8; LIVENESS.R3 |

### 4.3 Reactor, Liveness, and Process Topology

| Principle | Design consequence | Evidence | Contract |
|-----------|--------------------|----------|----------|
| One reactor, one arbiter, a closed set of inputs | Every task waits at one `PollingStrategy` arbiter driven by backend hints/notifications, local `notify_activity()` events, OS sentinel adapters, and explicit deadlines—never defensive polling wait-caps. The principle names today's realization (`PollingStrategy` and the four input origins); the identity statement is [WT-2a.6]. | Owner, 2026-09-17: "we are all event driven, with just a limited number of types of events" | [CC-2.2.1]; IMPL.8 to IMPL.11; [WT-2a.6] |
| A quiet task does nothing | When no event arrives and no deadline is due, a reactor sleeps without running turns. First wake after quiet is bounded by one pass of SimpleBroker's polling strategy, followed by burst mode; the strategy, not Weft, owns the pass length and burst behavior ([CC-2.2.1]). | Owner, 2026-09-17 | [CC-2.2.1] |
| Subprocess and stream supervision is readiness-driven, not timer-polled | When a runner supervises child stdio pipes alongside timeouts and resource limits, the platform readiness adapter drives wakeups; the supervision timeout is only the protective ceiling until the next deadline. Post-exit stream drain stops immediately when the kernel pipe reports EOF or empty rather than sleeping out a fixed drain window when an inherited grandchild descriptor remains open, and is otherwise bounded by the private drain budget while a descendant keeps writing. | Owner, 2026-10-29 | `06-Resource_Management.md` [RM-5.2]; `01-Core_Components.md` [CC-2.5], [CC-3.4] |
| Bounded per-turn stream work and backpressure over unbounded buffering | Active stream drainage reads a bounded chunk budget per turn so output floods cannot starve timeout, cancellation, or resource-limit checks, and slow downstream sinks apply OS pipe backpressure to the child rather than growing parent memory without bound. At process termination, remaining kernel pipe bytes are captured into memory before slow sink delivery. | Owner, 2026-10-29 | `06-Resource_Management.md` [RM-5.2]; `01-Core_Components.md` [CC-2.5] |
| Availability and death are different questions | A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death. Task-owned terminal proof wins over a Manager `wrapper_lost` record, and reservation/checkpoint custody survives until task-snapshot handoff or positive settlement. | Owner, 2026-09-08; `2026-09-17` discovery plan; `2026-10-14` family scheduling plan | [MA-1.6a], [MA-3], [MF-5], LIVENESS.R3–R7; [WT-2a.13] |
| Ask who owns this lifetime | A shared Manager, a child task launched detached from the Manager's lifetime, and a waiting client have independent lifetimes. Manager supersession changes who takes future work; it never kills or moves running children. | Owner, 2026-09-08 | [MA-1], [MA-3]; `14-Python_API_Surfaces.md` [PY-2]; [WT-2a.13] |

### 4.4 Simplicity, Evolution, and Verification

| Principle | Design consequence | Evidence | Contract |
|-----------|--------------------|----------|----------|
| As few concepts as possible | Changes consolidate onto existing concepts rather than introducing a second spawn lane, state store, event bus, scheduler daemon, or cache subsystem. | Owner, 2026-09-21: "We want to have as few concepts as possible"; 2026-10-14 | `00-Overview_and_Architecture.md`, Product Boundary |
| Shared function, shared code | Two code paths for one responsibility is a design defect. A subclass must justify why the shared base path cannot serve. | Owner, 2026-10-09 and 2026-09-21 | Engineering principles 1 and 4.2 |
| The expert Python developer test | A design must look clean, direct, and idiomatic to an expert Python systems engineer—e.g., constructing a helper with explicit immutable inputs rather than shallow-copying and mutating a live Task instance. | Owner, 2026-10-09, 2026-10-14, 2026-09-21 | `2026-09-22` MaintenanceWorker plan |
| No over-armoring | Engineer for real execution and failure boundaries, not impossible or adversary-invented states. Best-effort paths try, catch, and log diagnostics; operator-precondition violations belong in documentation, not in-band heuristics. | Owner, 2026-09-07, 2026-09-15, 2026-10-13 | Engineering principles, Secondary Rules |
| Measure; do not bake in inefficiency | Performance claims require measurements on both SQLite and PostgreSQL (including broker-operation counts across completion-to-next-launch intervals). | Owner, 2026-09-11, 2026-09-17, 2026-10-09 | `2026-10-12` burst plan; `2026-10-14` family scheduling plan |
| Tests prove correctness, nothing else | Tests verify observable behavior and state-machine transitions with real queues and processes—never pinned dependency versions, CI script order, or reduced concurrency (`-n 0`) to mask load bugs. | Owner, 2026-09-16, 2026-10-06, 2026-09-10, 2026-09-15 | `08-Testing_Strategy.md`; engineering principle 4 |
| No capability diminution | Simplifications must preserve observable capabilities and latency bounds; each hardening plan maintains an explicit difference register. | Owner, 2026-09-07; review rule in the 2026-09-08 plans | Hardening runbook |
| No compatibility code | Contract changes update all producers and consumers in one coordinated release with a bounded forward migration for durable queue data—no permanent dual decoders or silent fallback lanes. | Owner, 2026-09-17: "No need for backwards compat" | `00-Overview_and_Architecture.md`, Single current contract; [WT-2a.9] |
| One path on all platforms | Windows, macOS, and Linux share the same lifecycle and cleanup architecture; platform-specific branches are narrow adapter leaves. | Owner, 2026-10-07, 2026-09-09 | `2026-09-10` process-title plan |
| The client mirrors the CLI | The Python client and the CLI expose the same mental model and verbs over one shared command-capability layer that both adapters call. | Owner, 2026-09-23 | `11-CLI_Architecture_Crosswalk.md`; [PY-1] |
| Specs are what is; theory is where we go | When a spec reflects a flawed design, revise the spec through a class-5 plan; plans record execution status, while specs state intended behavior. | Owner, 2026-10-09 | `docs/agent-context/decision-hierarchy.md` |
| Observable with standard tools | Process titles and queue peeks are the operator interface; Weft never needs a daemon, index, or query service of its own to be inspected. | Owner, 2026-11-02 (a design goal, not an identity invariant); `CLAUDE.md` §9 | [WT-5] (no second control plane); [OBS.4] |

## What Weft is not [WT-5]

Weft is not a distributed workflow engine, a general DAG/join compiler, an
agent-management or prompt-orchestration layer, a provider-health monitor, or a
second control plane. It does not maintain a shared relational database of task
lifecycle state for Weft operations (`TaskMonitor`'s sidecar tables are private,
task-internal working state for log export and retention pruning, never read by
Weft's execution, control, or query paths), probe external tools before an
ordinary `weft run`, or invent application policy. See
`00-Overview_and_Architecture.md`, Product Boundary.

Weft does not own queue mechanics. Message IDs, ordering, atomic moves,
delivery claims, and storage transactions belong to SimpleBroker. If a problem
requires a queue-level primitive, that primitive belongs in SimpleBroker.

Weft operates within a single user/operator trust domain. The operating system,
filesystem permissions, and database grants are the security boundary—not
in-band command sanitizers or authenticated control messages. The owner stated
on 2026-09-17 that a hand-crafted `PING` "is not going to be a threat we are
going to worry about".

This is SimpleBroker's model, adopted: inputs that choose where the boundary
sits are trusted by placement, inputs that shape names inside it are checked
for shape, and intent is never inspected (sibling account: the security
section of SimpleBroker's configuration guide). Resource limits and sandbox
runners bound what a task may do; they do not decide whether a caller is
trusted. The identity statement is [WT-2a.11]. Boundary refusals are tagged
`Kind: boundary refusal` in [WT-6].

## Negative knowledge: rejected alternatives [WT-6]

Each record keeps a decision that the owner made against a plausible
alternative. An agent must not propose the alternative again unless the
"Reconsider when" condition is true. Records use the SimpleBroker
`[ALT-*]` form.

### [ALT-WT-001] A child lane and per-task connection budgets

- Kind: tried and discarded (the 0.9.110 child-lane experiment and `2026-10-13-connection-aware-child-admission-plan.md`, superseded; see [REV-WT-003])
- Disposition: rejected
- Candidate: A separate `weft.spawn.children` queue, a fixed prospective
  connection charge for each new task (eight slots even for `weft run ls`),
  and lifetime connection debt.
- Why plausible: A large nested burst (`rollup → enrichment → LLM`) exhausted
  PostgreSQL connections when all parents and children sat in one FIFO queue. A
  second lane for children plus a per-task connection reservation looked like a
  direct local fix.
- Reason: It applied a blunt ad-hoc patch instead of framing the problem as
  classical admission control + bounded depth-first task scheduling. The child
  lane recreated the same FIFO starvation one level deeper (enrichments starved
  LLMs), while the guessed eight-slot budget charged queued/lightweight work for
  connections it never opened. The owner, 2026-10-14: "this creates a special
  case when we really should not have one. What we need is better admission
  control over our existing primary lane, not adding a new lane."
- Current consequence: All ordinary work uses `weft.spawn.requests`, with
  measured admission (`available = ceiling - reserve - measured_usage`), detached
  lineage in `weft.state.tasks.<tid>`, and consume-on-dispatch pending queues in
  `weft.state.labels.<root_tid>.pending.<parent_tid>`. Active paths offer newest
  tip children first; independent families enter FIFO and sibling work may start
  another path. The owner rejected the replacement's five-path cap on
  2026-10-15 because it withheld work despite ample measured capacity.
- Reconsider when: Never as a second public lane or guessed per-task connection
  charge. If waiting parents exhaust capacity before descendants are submitted,
  investigate resource lifetime and scheduling evidence. Ordering alone cannot
  release their connections; do not silently reinstate a family cap.
- Evidence: `2026-10-13-connection-aware-child-admission-plan.md` (superseded);
  `2026-10-14-measured-admission-family-scheduling.md`;
  `2026-10-15-work-conserving-family-scheduling.md`.

### [ALT-WT-002] Fixed polling caps as a fallback

- Kind: tried and discarded (the caps were the earlier implementation and were removed by `2026-09-17-watcher-reactor-restoration-plan.md`; see [REV-WT-001])
- Disposition: rejected
- Candidate: Short fixed wait caps on worker, signal, parent-loss, and PONG
  discovery, so a reactor that missed an event still made progress.
- Why plausible: Before the event-driven path worked, the caps hid the gap.
- Reason: The caps were "accumulated cruft" (owner, 2026-09-17). Each local
  source now calls `notify_activity()`. An ordinary task has no timer. A
  service task supplies its own deadline through `next_wait_timeout()`.
- Current consequence: The first wake after quiet is one pass of
  SimpleBroker's polling strategy. After that the strategy is in burst mode;
  the strategy, not Weft, owns the pass length ([CC-2.2.1]).
- Reconsider when: A local source exists that cannot call the arbiter and has
  no adapter. Then add the adapter, not a cap.
- Evidence: `2026-09-17-watcher-reactor-restoration-plan.md`; [CC-2.2.1].

### [ALT-WT-003] A PONG found by a poll of the target's `ctrl_out`

- Kind: tried and discarded (the earlier PING path, replaced by `2026-09-18-event-routed-manager-pong-plan.md`)
- Disposition: rejected
- Candidate: The requester writes a PING, then scans the target's `ctrl_out`
  for the reply on a short timer.
- Why plausible: `ctrl_out` already existed and the target already wrote
  there.
- Reason: Pull, not push. The reply was not an event for the requester's
  reactor. On PostgreSQL the requester's listener did not watch that queue.
  The owner, 2026-09-17: "A PING always has a requester and a target. One
  code path."
- Current consequence: A PING names the requester's own `ctrl_in` as
  `reply_to`. The target writes one PONG there. The requester's existing
  control handler recognizes it.
- Reconsider when: Never. A new control reply follows the same shape: it is
  pushed to a queue the requester already watches, never found by scanning
  the target's queues.
- Evidence: `2026-09-18-event-routed-manager-pong-plan.md`; [MF-3].

### [ALT-WT-004] A heartbeat as the TaskMonitor clock

- Kind: tried and discarded (built and still registered today; decided by the owner on 2026-09-08; removal plan `2026-09-08-service-wake-model-and-heartbeat-decoupling-plan.md` is a draft)
- Disposition: rejected
- Candidate: The built-in heartbeat service sends a message on each interval,
  and TaskMonitor cycles when it receives one.
- Why plausible: A queue message is an event, and events drive reactors.
- Reason: TaskMonitor also kept a local deadline, so it had two clocks. A
  clock is a timer, not a message. Each service keeps its own clock through
  `next_wait_timeout()`. The heartbeat is an emitter for consumers that want
  one.
- Current consequence: The decision is made. The implementation is not. The
  plan is a draft. TaskMonitor still registers with the heartbeat service.
  The landed part is commit `da5fa0f6`: the Manager wants the heartbeat
  service only when TaskMonitor is enabled.
- Reconsider when: Never for a clock. A consumer that wants a periodic
  message can still register.
- Evidence: Owner decision, 2026-09-08; `2026-09-08-service-wake-model-and-heartbeat-decoupling-plan.md` (draft).

### [ALT-WT-005] A shallow copy of a task as a maintenance worker

- Kind: tried and discarded (built in TaskMonitor and replaced by `2026-09-22-task-monitor-maintenance-worker-plan.md`)
- Disposition: rejected
- Candidate: TaskMonitor made a shallow copy of itself, reset every mutable
  field, and ran maintenance on the copy in a worker thread.
- Why plausible: The copy had all the state the worker needed.
- Reason: The copy gave a worker the internals of a whole task. The field
  inventory had to stay in step in five places. The owner, 2026-09-21: "I
  might have created a new instance in a thread ... rather than doing a
  copy."
- Current consequence: `MaintenanceWorker` is a normally constructed private
  object. It receives explicit detached inputs and returns typed results
  through the existing worker-result channel.
- Reconsider when: Never. A worker gets explicit inputs, not a task copy.
- Evidence: `2026-09-22-task-monitor-maintenance-worker-plan.md`; IMPL.11.

### [ALT-WT-006] Task-level REQUEUE as a reserved-queue policy

- Kind: tried and discarded (shipped as a task policy and removed by `2026-08-31-reserved-disposition-and-requeue-removal-plan.md`)
- Disposition: removed
- Candidate: A task policy that moved a failed reserved row back to the
  inbox.
- Why plausible: A retry policy is common in job systems.
- Reason: Weft supports `keep` and `clear` for tasks. Only the Manager spawn
  lane requeues, and that is a policy-driven restore after a launch failure.
- Current consequence: Task policies are `keep` or `clear`. A failed row
  stays visible in the reserved queue.
- Reconsider when: Weft explicitly supports shared inboxes. The owner noted
  on 2026-09-10 that the rule "may need reexamination" in that case.
- Evidence: `2026-08-31-reserved-disposition-and-requeue-removal-plan.md`.

### [ALT-WT-007] A stopping drain, or a resuming drain, on supersession

- Kind: tried and discarded (the stopping drain shipped and cancelled user work during concurrent bootstrap; see the `2026-05-20` lesson in `docs/lessons.md`)
- Disposition: rejected
- Candidate: (a) When a Manager observed its own `superseded` row, it reused
  the ordinary STOP drain and stopped its children. (b) A superseded Manager
  that had entered its drain resumed dispatch if the replacement Manager
  disappeared.
- Why plausible: (a) One drain path is simpler than two. (b) A vanished
  replacement leaves no active Manager for a window.
- Reason: (a) The STOP drain cancelled user tasks during concurrent
  bootstrap. A supersession notice changes who takes future work; it does
  not move or stop existing work. (b) The superseded Manager never holds
  better information than the row it read, and the gap is closed by the
  next submission: the ensure-manager path sees no active Manager and
  starts a fresh one. The leadership-yield drain revalidates and may resume
  because it acts on self-observed, possibly stale leader proof; the
  superseded drain acts on a row a live peer just wrote. Those are two
  conditions with one policy each, not one condition with two.
- Current consequence: A `superseded` row starts a one-way, non-stopping
  drain: no new intake, owned children run to completion, then the Manager
  exits. Explicit operator replacement (`--replace`) and automatic
  convergence share that drain. The owner stated the rule on 2026-11-02:
  "What should happen is that A continues draining and exits. The next
  call that requires a manager should see that there is no active manager
  and should start one (manager C), which becomes the new primary manager."
- Reconsider when: a submission path exists that cannot start a Manager
  when none is active, or a superseded Manager is shown to hold evidence
  the registry row lacks.
- Evidence: Owner dialogue, 2026-09-08 and 2026-11-02; [MA-1.4], [MA-3];
  `2026-05-20` lesson in `docs/lessons.md`; `[REV-WT-004]`.

### [ALT-WT-008] A Manager reuse flag

- Kind: tried and discarded (the flag shipped and is inert since the RunSession migration; decided by the owner on 2026-09-08; retirement plan `2026-09-08-manager-reuse-flag-retirement-and-run-lifecycle-plan.md` is a draft)
- Disposition: rejected, removal not yet landed
- Candidate: `WEFT_MANAGER_REUSE_ENABLED`, a configuration key that let a
  caller refuse an existing Manager.
- Why plausible: Tests wanted a fresh Manager.
- Reason: The flag is test-only and inert after the RunSession migration. A
  Manager belongs to the context, not to the invocation that started it.
- Current consequence: The constant is still defined in Weft's constants
  module and is always `True`. The retirement plan is a draft. `weft run`
  finds a Manager or starts one.
- Reconsider when: Never.
- Evidence: Owner decision, 2026-09-08;
  `2026-09-08-manager-reuse-flag-retirement-and-run-lifecycle-plan.md` (draft).

### [ALT-WT-009] Every waiter scans the shared task log for completion

- Kind: tried and discarded (the earlier waiter design, replaced by `2026-10-12-burst-submission-complexity-plan.md`; see [REV-WT-002])
- Disposition: rejected
- Candidate: A result waiter reads each new row in `weft.log.tasks` and
  filters for its TID.
- Why plausible: The log is the lifecycle record, and the row is there.
- Reason: W waiters and E shared events cost O(W E) work, precisely during a
  burst. A completion fact must go to its observers.
- Current consequence: The task publishes one serialized completion record to
  the log and to `ctrl_out` in one atomic broadcast. A waiter with known
  configuration watches its own queues only.
- Reconsider when: Never for ordinary waits. Explicit lifecycle-event
  following still reads the log, and says so.
- Evidence: `2026-10-12-burst-submission-complexity-plan.md`; [MF-5], OBS.1.

### [ALT-WT-010] A new scheduler, event bus, index, or cache subsystem

- Kind: boundary refusal (refused as a second lifecycle database; the entry records that the fixes "were applications of the existing theory, not new machinery", so no subsystem was built) See [WT-5].
- Disposition: rejected
- Candidate: A general subsystem to make repeated discovery cheap.
- Why plausible: An index or a cache removes repeated work.
- Reason: A cache that becomes startup truth is a second lifecycle database.
  The fixes were applications of the existing theory, not new machinery.
- Current consequence: No heaps, counters, or registries exist only to claim
  constant cost. In-memory state stays a hint.
- Reconsider when: A cost measured on both SQLite and PostgreSQL remains
  after every existing primitive is used correctly, and the owner accepts a
  named new concept.
- Evidence: `2026-10-12-burst-submission-complexity-plan.md`, invariant 8.

### [ALT-WT-011] Hidden preflight on the ordinary execution path

- Kind: boundary refusal (product boundary: a hidden probe pulls Weft into agent management; `00-Overview_and_Architecture.md`, Product Boundary) See [WT-5].
- Disposition: rejected
- Candidate: `weft run` probes whether a binary, a provider, or an image can
  run before it submits the task.
- Why plausible: An early failure is easier to read than a runtime failure.
- Reason: A hidden probe pulls Weft up into agent management. The ordinary
  path attempts the real run and reports the concrete failure.
- Current consequence: Executability checks are opt-in diagnostic surfaces.
- Reconsider when: Never on the ordinary path.
- Evidence: `docs/agent-context/engineering-principles.md`, Secondary Rules
  and section 8.

### [ALT-WT-012] Backward-compatibility code for changed contracts

- Kind: boundary refusal (identity: single current contract; owner decisions 2026-09-08 and 2026-09-17) See [WT-5].
- Disposition: rejected
- Candidate: Legacy decoders, tolerance windows, and dual-shape readers after
  a queue shape or API change.
- Why plausible: Old rows and old callers exist.
- Reason: Dual readers hide contract bugs and cascade. The users are
  internal. One coordinated release updates all producers and consumers. A
  bounded forward migration handles durable old data.
- Current consequence: "Single current contract" in the overview
  specification. The CHANGELOG records each break.
- Reconsider when: Weft has external users on a published compatibility
  promise.
- Evidence: Owner decisions, 2026-09-08 and 2026-09-17; Golden Rules 1 to 3.

### [ALT-WT-013] Line count as a complexity measure

- Kind: boundary refusal (a generic review heuristic refused on cohesion grounds; `CLAUDE.md` section 1.1, engineering principle 11) See [WT-5].
- Disposition: rejected
- Candidate: Split large modules because they are large.
- Why plausible: Generic review heuristics call a large file a "god class".
- Reason: Agents read by grep and offset. A large cohesive file is a
  pre-joined index. A false seam breeds parallel implementations. Cohesion
  and named state machines are the floors, not line counts.
- Current consequence: `CLAUDE.md` section 1.1 and engineering principle 11.
- Reconsider when: Never on size alone. Extract when a named state machine
  needs its own contract test.
- Evidence: `CLAUDE.md`; `docs/agent-context/engineering-principles.md` 11.

### [ALT-WT-014] An input sanitizer as a security boundary

- Kind: boundary refusal (trust-domain and argv-only identity; no such helper was built) See [WT-5].
- Disposition: rejected
- Candidate: A `validate_safe_path` style helper that rejects dangerous paths.
- Why plausible: Shell injection is a common risk.
- Reason: There is no shell. Commands are argv lists. A sanitizer implies a
  boundary that does not exist.
- Current consequence: Validate at the boundary once, then stay strict.
- Reconsider when: Weft stops using argv-only execution.
- Evidence: `CLAUDE.md` section 4.11.

### [ALT-WT-015] Manager-local child tracking or OS `ppid` as the liveness/lineage source

- Kind: boundary refusal (layer and identity grounds: detached children outlive their Manager by design, [MA-1], [MA-3]; owner 2026-09-23 and 2026-10-14) See [WT-5].
- Disposition: rejected
- Candidate: Use the Manager's in-memory child table or OS parent-PID trees
  to decide whether a task is alive and which branch it belongs to.
- Why plausible: The Manager started the child and holds its `BaseProcess`
  handle while the Manager remains alive.
- Reason: Submitted children are never forked by their submitting parent task,
  and ordinary user tasks are launched detached from the Manager's lifetime,
  so they survive Manager drain, idle exit, crash, or replacement by design
  ([MA-1], [MA-3]). The Manager's in-memory table is only a local hint. Task-owned
  `weft.state.tasks.<tid>` snapshots (with pre-handoff custody in
  `T{manager}.reserved` + `weft.state.tasks.<manager_tid>`) are the durable
  bridge between scoped `(pid, create_time)` process identity and task lineage
  (`root_tid`, `submitter_tid`, `ancestry_tids`, `scheduling_class`).
- Current consequence: Task status and recovery read `weft.state.tasks.*`
  through the one shared newest-valid snapshot reader that core owns under
  [OBS.6] and [MF-5]; that reader never deletes. A terminal event alone never
  proves process exit, and `LivenessMonitor` is the sole component deleter of
  `weft.state.tasks.*` ([LIVENESS.R3]).
- Reconsider when: Never.
- Evidence: Owner, 2026-09-23 and 2026-10-14; `07-System_Invariants.md`
  OBS.6, LIVENESS.R3–R7.

### [ALT-WT-016] Re-derive the task graph on each dispatch or bulk-move pending tokens on parent exit

- Kind: tried and discarded (investigated in `2026-10-14-measured-admission-family-scheduling.md` and refused on its O(N) completion-to-next-launch cost, owner 2026-10-14)
- Disposition: rejected
- Candidate: Have the Manager scan all `TaskSpec`s or `weft.state.tasks.*`
  snapshots to reconstruct family ancestry on every dispatch, or move every
  queued child token out of a parent's pending bucket when that parent exits.
- Why plausible: All lineage information exists in the queues, and moving
  remaining tokens to a grandparent bucket on parent exit keeps bucket names
  aligned with live parents.
- Reason: "Sounds like a lot of re-work" (owner, 2026-10-14). Scanning all
  queued requests on dispatch or moving $N$ pending tokens when a parent exits
  makes completion-to-next-launch work $O(N)$ in backlog size.
- Current consequence: Pending requests are indexed incrementally once into
  `weft.state.labels.<root_tid>.pending.<parent_tid>`, carrying immutable
  `ancestry_tids`. When `<parent_tid>` exits, the Manager updates the bucket's
  contracted scheduling-parent pointer in $O(1)$ in memory and continues
  consuming `.pending.<parent_tid>` directly as children are dispatched.
- Reconsider when: Never for per-dispatch scans or $O(N)$ exit moves. Bounded,
  resumable reconciliation runs amortized across maintenance turns to heal
  cursor holes.
- Evidence: `2026-10-14-measured-admission-family-scheduling.md`.

### [ALT-WT-017] Reduced test concurrency as an acceptance gate

- Kind: tried and discarded (lowered concurrency was the practice until the rule was revised on 2026-10-14; owner 2026-09-09 and 2026-09-15)
- Disposition: rejected
- Candidate: Run the suite with `-n 0` or a low worker count to get a green
  result.
- Why plausible: Load causes some failures.
- Reason: "If load causes failures, that is in itself a signal of problems"
  (owner, 2026-09-09). The urge to relieve contention to obtain green means the
  investigator's explanatory model has not accounted for the observed behavior.
  Lower load hides that counterexample; it does not restore possession of the
  theory. The conceptual gap may be in the product, harness, oracle, substrate,
  or assumptions about the environment. It must be located, not presumed.
- Current consequence (revised 2026-10-14; was: lower concurrency allowed for
  debugging without an explicit stop): Stop corrective work and acceptance
  reruns immediately, preserve the original failure and load conditions, and
  re-derive the expected behavior and its causal owner. Only then may a stated
  falsifiable hypothesis use lower load as a diagnostic experiment. Its pass
  never closes the original failure. Verify the justified correction at the
  original pressure and standard concurrency, against the corrected contract
  if the oracle was wrong. See Testing Strategy [TS-0] and testing-patterns
  Pattern 1 for the operating rule.
- Reconsider when: Never as a weaker acceptance gate.
- Evidence: Owner, 2026-09-15 and 2026-10-14; `docs/lessons.md`;
  `docs/specifications/08-Testing_Strategy.md` [TS-0].

### [ALT-WT-018] Tests that pin process or dependency versions

- Kind: tried and discarded (such tests existed and were deleted or retargeted; owner 2026-10-06 and 2026-09-16)
- Disposition: rejected
- Candidate: A test that asserts an exact dependency version, a CI step
  order, or a release script detail.
- Why plausible: The test catches a drift early.
- Reason: "A regular update should not break things" (owner, 2026-10-06). The
  test encodes process, not correctness.
- Current consequence: Such tests are deleted or retargeted to behavior.
- Reconsider when: A version is a correctness floor with a known failure
  below it. Then the floor lives in `pyproject.toml`, not in a test.
- Evidence: Owner, 2026-10-06, 2026-09-16; test-audit skill.

### [ALT-WT-019] Using SimpleBroker queues as unconsumed relational tables or event logs

- Kind: tried and discarded (the initial replacement draft in `2026-10-14-measured-admission-family-scheduling.md`, refused in owner and architectural review on 2026-10-14; see [REV-WT-003])
- Disposition: rejected
- Candidate: Store family membership lifecycle events (`accepted`, `launched`,
  `snapshot`, `terminal` with foreign-key `source_ref` pointers) and versioned
  `frontier` / `state: "released"` tombstone records in an append-only
  `weft.state.labels.<root_tid>` queue, while leaving `pending` rows unconsumed
  in `.pending.<submitter_tid>` until a four-condition family retirement fence
  manually deletes them via `exact-delete`.
- Why plausible: Detached child processes outlive their Manager, so the
  scheduler needed durable cross-Manager lineage and frontier state; an
  event-sourced family log in `weft.state.labels.*` appeared self-contained.
- Reason: In SimpleBroker, queues are created on write and **automatically
  disappear when emptied**. Leaving rows unconsumed until a post-completion
  garbage-collection sweep turned queues into manual relational tables, and
  duplicated state already owned by `weft.state.tasks.<tid>` (deleted by
  `LivenessMonitor` on process exit) and `weft.state.tasks.<manager_tid>`.
- Current consequence: Detached task lineage (`root_tid`, `submitter_tid`,
  `ancestry_tids`, `scheduling_class`, `connection_owner`) lives in
  `weft.state.tasks.<tid>`; active frontier checkpoints (`scheduler_frontiers`)
  live in `weft.state.tasks.<manager_tid>`; and
  `weft.state.labels.<root_tid>.pending.<parent_tid>` is a true
  consume-on-dispatch queue whose tokens are deleted as soon as the referenced
  `weft.spawn.requests` row is reserved, so pending queues empty and vanish
  automatically in SimpleBroker.
- Reconsider when: Never. Runtime queues must either be consumed as work
  advances or be owned by `LivenessMonitor`'s single-deleter lifecycle.
- Evidence: Owner & architectural review, 2026-10-14;
  `2026-10-14-measured-admission-family-scheduling.md`.

### [ALT-WT-020] Reading task-internal state (`MonitorStore`) from Weft status or result paths, or forcing stream-collation state into queues

- Kind: tried and discarded (status and result paths read `MonitorStore` until `2026-10-14-task-monitor-read-separation-and-simplification-plan.md` removed it)
- Disposition: rejected
- Candidate: Either (a) have `weft status`, `weft result`, or task discovery
  query `TaskMonitor`'s sidecar collation tables (`MonitorStore`) as a fallback
  lifecycle store when queue rows have aged out, or (b) eliminate `MonitorStore`
  by storing `TaskMonitor`'s collation and export cursors as synthetic
  `weft.state.*` queues.
- Why plausible: (a) `MonitorStore` already collates `weft.log.tasks` in the
  same SQL database; (b) "State lives in queues, not databases" can be misread
  as forbidding any task from keeping non-queue internal working state.
- Reason: Both options confuse **Weft substrate state** with **task-internal
  working state**. Individual tasks may maintain their own internal state to do
  their job (`TaskMonitor` is a task that transforms `weft.log.tasks` into
  external JSONL logs and prunes covered queue rows, using `broker.sidecar()`
  tables for its private stream-collation state), but **Weft must never depend
  on task-internal state for its operation** (owner, 2026-10-15). Reaching into
  `MonitorStore` from `status` or `result` promoted one task's private state
  into a second system-wide lifecycle database, while forcing `TaskMonitor`'s
  collation state into queues would turn queues into unconsumed relational
  tables ([ALT-WT-019]).
- Current consequence: Weft read and control paths (`status`, `result`,
  `Manager`, `LivenessMonitor`) read only queues (`T{tid}.*`, `weft.log.tasks`,
  `weft.state.*`) and never import or query `MonitorStore`. `MonitorStore`
  remains strictly private to `TaskMonitor` for external log export and
  retention pruning.
- Reconsider when: Never. Weft operations must work identically from queues
  alone whether `TaskMonitor` is running or disabled, and task-internal state
  must remain encapsulated within the task that owns it.
- Evidence: Owner, 2026-10-14 and 2026-10-15;
  `2026-10-14-task-monitor-read-separation-and-simplification-plan.md`;
  `2026-10-14-task-monitor-ingest-and-retirement-plan.md`;
  `2026-10-13-supervisor-terminal-projection.md`; [OBS.13], [SB-0.4a].

### [ALT-WT-021] Non-blocking pipe polling loops in subprocess runners

- Kind: tried and discarded (the polling adapter shipped and was measured and replaced; commits `4c490043` and `d4381877`, `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`)
- Disposition: rejected
- Candidate: Setting subprocess `stdout`/`stderr` descriptors non-blocking and
  polling them on a fixed short interval in helper threads so pipe `close()`
  and post-exit grandchild pipe leaks do not hang `TextIOWrapper`.
- Why plausible: Avoided blocking `TextIOWrapper.read()` deadlocks on pipe
  close and worked across POSIX and Windows without platform-specific handle
  APIs.
- Reason: Spends wakeups and context switches on every idle subprocess at a
  fixed polling rate, couples output latency to a polling quantum, buffers child
  output unboundedly in parent memory during slow callbacks, and violates the
  single-arbiter readiness principle ([WT-2], [ALT-WT-002]).
- Current consequence: Subprocess pipe supervision sits behind an opaque
  platform readiness adapter: POSIX demultiplexes non-blocking descriptors on
  the supervisor thread with no helper threads, child exit joins the selector
  through a pidfd on Linux or a kqueue process filter on macOS and BSD (never a
  thread parked in `Popen.wait()`, which holds the waitpid lock and blinds
  `poll()`), and Windows waits
  on overlapped pipe completion events together with the process handle, with
  no helper threads and cancellation through the overlapped operation itself.
  A synchronous-handle design (blocking reader threads torn down with
  `PeekNamedPipe` and `CancelSynchronousIo`) was rejected in review: the peek
  can block behind a reader and the cancel cannot reach a read not yet
  submitted. Child exit is always its own input; pipe EOF is never a proxy
  for it.
- Reconsider when: Never. If an OS handle cannot join a readiness selector
  directly, wrap it in a blocking source adapter ([ALT-WT-002]), never a
  polling timer floor.
- Evidence: Owner, 2026-10-29; measurement baseline commit `540707a3`, with
  commit `4c490043` (adapter introduction) and commit `d4381877` (adapter
  replacement); `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`,
  which records the polling adapter's measured idle-child cost.

## Tensions and falsifiers [WT-7]

- **Possession probe, keyed:** At each release or class-5 completion, the
  owner writes the key first: one hypothetical feature and where it belongs,
  one category error and the entry that refuses it, and the class of defect
  an audit of a named subsystem should find. An agent that did not author
  the plan, from a different family where one is available, answers from
  this document alone and is graded against the key. The key and the result
  go in the plan's review log; a drawn key item is then spent. On FAIL,
  classify after disposition as a document deficiency, a key fault, or
  agent reasoning, and change this document only for a document deficiency.
  A self-graded probe is not a probe. Refresh the `[WT-0]`
  **theory-versus-code audit table** at the same trigger, carrying unchanged
  rows forward by reference and re-auditing touched claims.
- **Contention failures test possession of the theory:** Wanting fewer workers,
  serialization, or another reduction in contention to make tests pass is an
  immediate stop condition. State the behavior the theory predicted and locate
  the conceptual flaw or false premise before accommodating the failure. A pass
  alone does not explain a failure under load; calling it "flaky" or
  "infrastructure contention" does not supply the missing model. This is a
  falsifier of the investigator's current possession, not proof that a specific
  runtime component is broken. [ALT-WT-017] and Testing Strategy [TS-0] define
  the diagnostic and acceptance boundaries.
- **Blunt local mechanism vs. classical CS model:** When a proposal invents a
  new queue lane, a guessed per-task resource charge, a polling timer, or an
  unconsumed state table to fix a local symptom, treat that as a falsifier
  signal: the author has not yet named the underlying CS problem (scheduling,
  admission backpressure, event demultiplexing, custody handoff) or mapped it
  onto Weft's existing queue and process primitives.
- **Simplicity against ambition:** Weft adds agent runtimes, restricted
  runners, pipelines, and work-conserving family scheduling. Each addition must still
  compose as tasks and queues. If a Weft coordination, scheduling, or query
  feature requires a second control plane or depends on a task's internal
  sidecar state, the theory or the feature is wrong.
- **Inspection needs Weft-only tooling:** If inspecting a running Weft
  requires a Weft-specific daemon or query service, or shows state that no
  queue peek can reach, a second control plane has appeared. Task-private
  working state ([WT-2a.2]) is outside this test; Weft's operational state is
  not.
- **Event-driven against absence:** Timers exist only for clock work or for
  absence detection (such as missing best-effort startup snapshots). Each new
  timer must answer: *which event cannot arrive, and why?*
- **Measured admission is a soft guard:** PostgreSQL enforces its own server
  and role connection limits, and external clients share the server. The theory
  does not promise exact per-task connection forecasting. It measures real
  pressure ($U$) against the backend ceiling minus a global reserve, without a
  separate family cap. On PostgreSQL, automatic native capacity is the default.
  Waiting parents retain connections, so arbitrary delayed descendants have no
  unconditional progress guarantee.
- **Embedder friction as a missing-seam signal:** Repeated use of private
  internals by an embedder (`engram`, `taut`, `weft_django`) is evidence that a
  public capability seam is missing, not evidence that the embedder is wrong.
- **Repeated rejected proposals as a transfer failure:** A repeated proposal
  for a lane, a cache, a compatibility shim, or an unconsumed queue table is
  evidence that a principle in `[WT-4]` or an `[ALT-WT-*]` record is not clear
  enough. Sharpen the theory rather than accepting the proposal. Log rule: a
  review proposal that matches a `[WT-6]` entry and does not cite that
  entry's recorded reopening condition is written up in the review's
  disposition row as a transfer failure against the entry; a proposal that
  cites a fired reopening condition is a legitimate reopening, not a failure.
- **The "expert Python developer" test vs. measured cost:** When naive
  simplicity causes $O(W \cdot E)$ or $O(N^2)$ burst amplification, resolve the
  tension by choosing the right algorithm (atomic observer broadcast, $O(1)$
  consume-on-dispatch buckets) while keeping one durable source of truth.
- **Altitude collapse (unfalsifiable aphorisms or implementation lock-in):** A
  written program theory fails when its claims sit at the wrong level of
  abstraction (`[REV-WT-004]`):
  - *Too abstract (aphorism list):* If the file reads like generic engineering
    proverbs without concrete concept ownership, identity invariants, composable
    operations, or discarded paths—so that two incompatible designs can both
    cite a sentence as support—it cannot place features or refuse bad fits.
  - *Too concrete (implementation lock-in):* If an internal refactor that
    preserves the product's problem, feel, boundaries, and identity invariants
    repeatedly invalidates `docs/program-theory.md`, the file has drifted into
    an implementation note or behavioral spec.
- **Module theory used to excuse a divergent subsystem:** Reaching for a
  `MODULE-THEORY.md` can be a symptom that a package has invented its own
  competing concepts, command grammar, or lifecycle rules instead of composing
  from the product's core operations (`[REV-WT-004]`). Before granting a
  subsystem its own theory file, check whether the subsystem should instead be
  simplified back into the product's model; a module theory is warranted only
  when local depth genuinely specializes the product theory rather than carving
  out an exemption from it.
  Extending the project theory to include one or more module theories is
  ultimately the human's decision. Module theories should not be used to justify
  variances from the program theory when one theory really should apply to the
  whole program. Module theories should only be used when a specific module is
  weakly coupled to the rest of the program and has theory-level invariants that
  should only apply to that module.

## Founding continuity [WT-8]

The first agent file in this repository, commit `4ef911ea` (2026-02-10),
stated the model in one line: "Everything is a Task. The Manager is a Task
that spawns other Tasks. Queues are the only communication channel. State
lives in queues, not databases." Every principle in [WT-4] is an
application of that line.

| Current principle | Founding text | Maintained | Evolved |
|-------------------|---------------|------------|---------|
| Everything is a task | "Everything is a Task" | Still the first rule | Agents, pipelines, services, and short-lived requesters are tasks too |
| Queues carry facts; consumed queues disappear | "State lives in queues, not databases" | Still the only durable record for Weft | Runtime-state queues (`weft.state.tasks.*`) hold detached process/lineage snapshots deleted by `LivenessMonitor`; pending family queues (`weft.state.labels.*.pending.*`) are consumed on dispatch so they vanish automatically; completion facts broadcast to `ctrl_out`; individual tasks (such as `TaskMonitor`) may hold private task-internal state (`MonitorStore`), which Weft never reads or depends on |
| Specs are the contract | "The specs are the source of truth. Code must align to them." | Still the contract | The theory now sits above the specs for design judgment; a spec that contradicts the theory changes through a planned change, and the theory sentence becomes that plan's acceptance test ([WT-0]) |
| Small observable substrate | "the durable task substrate for agent systems" (`CLAUDE.md`) | Still the product boundary | Measured admission, ancestry scheduling, labels, and sessions were added as classical algorithms over existing primitives without a second control plane |

## Revisions [WT-9]

Revision records put the current account first so historical material serves as
evidence of why the theory evolved rather than a competing model.

### [REV-WT-001] Single-arbiter event reactor replaced defensive polling wait-caps

- Current account: Every task is an event-driven reactor waiting on one
  SimpleBroker `PollingStrategy` arbiter over a closed set of inputs (backend
  queue notifications/hints, local `notify_activity()` completions, OS sentinel
  adapters, and explicit service deadlines). A quiet task does zero turns and
  uses no defensive fixed polling caps.
- Supersedes: Earlier watcher and control paths clamped wait timeouts with
  short fixed polling caps ([ALT-WT-002]) so missed local transitions would
  still be noticed on the next tick.
- Pressure: Polling caps masked missing event notifications, burned CPU and
  broker reads while idle, and obscured which local sources failed to notify
  the wake arbiter.
- Evidence:
  - contemporaneous: `docs/plans/2026-09-17-watcher-reactor-restoration-plan.md`
    and `docs/plans/2026-09-18-event-routed-manager-pong-plan.md`
  - contemporaneous: `docs/specifications/01-Core_Components.md` `[CC-2.2.1]`
    and `docs/specifications/07-System_Invariants.md` `IMPL.8`–`IMPL.11`
  - owner-recalled: owner sessions 2026-09-17 ("we are all event driven, with
    just a limited number of types of events")

### [REV-WT-002] Observer-addressed completion broadcast replaced shared task-log scanning

- Current account: A completing task publishes its terminal record atomically to
  `weft.log.tasks` and `T{tid}.ctrl_out` in a single broadcast. Result and
  completion waiters watch their target task's own queues (`T{tid}.outbox`,
  `T{tid}.ctrl_out`) rather than scanning `weft.log.tasks`.
- Supersedes: Waiters previously followed the global `weft.log.tasks` stream and
  filtered every task's lifecycle event by TID.
- Pressure: Under burst load, $W$ concurrent waiters scanning $E$ shared log
  events caused $O(W \cdot E)$ read amplification and connection contention
  precisely when capacity was tightest.
- Evidence:
  - contemporaneous: `docs/plans/2026-10-12-burst-submission-complexity-plan.md`
  - contemporaneous: `docs/specifications/05-Message_Flow_and_State.md`
    `[MF-3]`, `[MF-5]` and `docs/specifications/07-System_Invariants.md` `OBS.1`
  - owner-recalled: owner session 2026-10-09

### [REV-WT-003] Measured admission and work-conserving family scheduling replaced child lanes and prospective connection budgets

- Current account: All ordinary root and child submissions enter
  `weft.spawn.requests` with zero prospective connection charge while queued.
  The Manager gates process creation on measured capacity
  (`available = C - R - U`), using the backend maximum by default on PostgreSQL.
  Active paths offer newest tip children first, followed by FIFO independent
  families and oldest unassigned siblings. Round-robin path turns provide work
  sharing without a family-count cap. Progress requires eventual real headroom
  for each necessary launch, since waiting parents can retain resources.
  Detached running task lineage (`root_tid`, `submitter_tid`, `ancestry_tids`,
  `scheduling_class`) lives in `weft.state.tasks.<tid>` (protected before
  handoff by `T{manager}.reserved` + `weft.state.tasks.<manager_tid>`), while
  per-parent pending indexes in
  `weft.state.labels.<root_tid>.pending.<parent_tid>` are consumed on dispatch
  so they empty and auto-disappear in SimpleBroker.
- Supersedes: The 0.9.110 experiment routed same-context child submissions to a
  separate `weft.spawn.children` FIFO queue and charged each launched task a
  prospective per-task connection budget (`WEFT_ADMISSION_TASK_CONNECTION_BUDGET`,
  defaulting to 8 slots) with lifetime deficit accounting; an initial
  replacement draft also proposed storing unconsumed `member`/`frontier` event
  logs in `weft.state.labels.<root_tid>`.
- Pressure: In production (`rollup → enrichment → LLM`), the shared children
  FIFO recreated parent/child starvation one level deeper (43 enrichments
  launched ahead of 60 queued LLMs) while prospective budget deficits inflated
  accounted connections to 267 against 130 actual sessions. Subsequent
  architectural review showed that unconsumed `weft.state.labels.<root_tid>`
  event logs defeated SimpleBroker's auto-disappearing queues and duplicated
  `weft.state.tasks.*`. The replacement initially retained five active paths,
  but that cap left resource capacity idle. The owner removed it on 2026-10-15.
- Evidence:
  - current correction: `docs/plans/2026-10-15-work-conserving-family-scheduling.md`
  - contemporaneous: `docs/plans/2026-10-14-measured-admission-family-scheduling.md`
    (draft, superseding `docs/plans/2026-10-13-connection-aware-child-admission-plan.md`)
  - owner-recalled: owner session 2026-10-14 on classical queuing/scheduling
    primitives and auto-disappearing SimpleBroker queues

### [REV-WT-004] Four-way balance, directional triad, and module-divergence falsifier

- Current account: [WT-0] states the directional triad (the program theory is
  the ideal; specs describe what is currently in the code; plans morph the
  code and specs toward the ideal) and four alignment rules beside the
  owner's 2026-10-09 rule. [WT-7] adds the "Altitude collapse" and "Module
  theory used to excuse a divergent subsystem" falsifiers, and the possession
  probe refreshes the theory-versus-code audit table. This part was
  propagated from agent-theory @ `e6ac579` (`[REV-AT-006]`) by
  `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` Slice A.
  Slice B account (identity invariants, the [WT-4] whole-shape reconciliation, [WT-6] kinds, the
  Verification line): [WT-2a] states fourteen identity invariants the owner
  endorsed on 2026-11-02, generalized where Taut's reactor over workspace
  reactors showed an invariant holds per owner; [WT-4] rows cite the
  invariant they instantiate; every [WT-6] entry carries a Kind tag and
  [ALT-WT-007] is restated; the [WT-0] Verification line names four
  measures; the [WT-7] probe is keyed.
- Supersedes: A surfaces table in which plans answered "What change was made
  or proposed", a read-order entry saying the theory "does not override
  winning contracts", and a module-theory rule that admitted module theory
  whenever depth was local. Slice B supersessions: identity scattered across
  [WT-2], [WT-4], and the agent entry files; a self-graded possession probe;
  the reversibility claim in [ALT-WT-007]; a Kind column on [WT-4]; and
  "one reactor per task", now one arbiter per reactor with composition by
  messages.
- Pressure: The hub's `[REV-AT-006]`, whose pressure came from Weft's
  2026-10-29..2026-11-01 theory crystallization and audit, where "theory
  never overrides specs" was used to defend existing specs against the
  theory. Verification pressure: the possession probe ran twice in its first
  eight weeks; neither record shows a preregistered key, who wrote the
  expected answers, or what they were graded against, and no release ran
  it; the keyed form above replaces it, with the audit table on the same
  trigger. Slice B pressure: the 2026-11-01 deep dive found the theory at
  mixed altitude (source paths, private names, millisecond figures), nine
  concepts without ownership rows, and three places where a specification
  sanctioned what the theory refused; the owner's four-way standard
  (general enough to measure a re-implementation, specific enough to decide
  what belongs, identity invariants, negative knowledge) set the bar.
- Evidence:
  - contemporaneous: `../agent-theory/docs/program-theory.md` `[REV-AT-006]`
    and `../agent-theory/docs/specs/02-agent-theory-and-program-theory.md`
    at `e6ac579`
  - contemporaneous: `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
    Slice A
  - contemporaneous: the plan's theory-versus-code audit table (46 rows: 37
    aligned, 8 drifted plus one ordering half, 1 theory revision), eight
    cross-family review rounds, and the 2026-11-02 keyed possession probe
    (PASS, three of three) in the plan's review log.

## Sources

- Owner sessions with coding agents, 2026-09-01 to 2026-10-14, in the
  `weft` and `simplebroker` working directories. Dates in this document name
  the session in which the owner stated the rule.
- `docs/plans/2026-10-12-burst-submission-complexity-plan.md`, the first
  written use of "weftness".
- `docs/plans/2026-09-17-watcher-reactor-restoration-plan.md`, Design Rule.
- `docs/plans/2026-09-18-event-routed-manager-pong-plan.md`.
- `docs/plans/2026-09-22-task-monitor-maintenance-worker-plan.md`.
- `docs/plans/2026-10-14-measured-admission-family-scheduling.md` (draft).
- `../simplebroker/docs/program-theory.md`, the model for this document.
- `../agent-theory/docs/program-theory.md`, the process model.

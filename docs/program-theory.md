# Weft Program Theory

Status: Draft, first version, not yet adopted
Owner: Weft product owner
Boundary: The identity of Weft and the rules for design judgment. Not the
exact current behavior. The specifications in `docs/specifications/` give the
exact current behavior.
Verification: Owner review and one independent semantic review. A possession
probe (see [WT-7]) one time for each release or each class-5 plan.
Required action: Read this document before you make a product-scope or
design decision. Obey it, or propose a theory revision as a class-5 change.
Governing process: None adopted in this repository yet. SimpleBroker uses
`[DOM-16]` in its operating-model specification for the same purpose.

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
50 ms polling cap, splitting a cohesive state machine by line count, or using
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
| Program theory (this file) | What kind of system is Weft? Why do its concepts, algorithms, and boundaries exist? |
| Specifications (`docs/specifications/`) | What is the exact intended behavior now? |
| Plans (`docs/plans/`) | What change was made or proposed, under what evidence and premises? |
| Code and tests | How is the behavior realized, and what executable evidence fires against each claim? |
| Lessons (`docs/lessons.md`) | What durable rule did a surprise or repeated failure establish? |

The owner stated the rule governing the first two rows on 2026-10-09: "The
specs describe what we have. The theory describes what we are building toward.
We are not trying to reify bad decisions." If a specification contradicts the
theory, the specification is revised through a planned change; the theory does
not bend to ratify an accidental or flawed implementation.

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
   If a lifecycle fact is durable, it is durable because a message sits in a
   SimpleBroker queue. In-memory structures inside a Manager or Consumer are
   performance hints or local process handles—never a second lifecycle database.
   Crucially, SimpleBroker queues are **auto-created on first write and
   auto-disappear when emptied** (`move_one` or `delete`). Weft uses per-task
   and per-branch queues (`T{tid}.*`, `weft.state.labels.<root_tid>.pending.<parent_tid>`)
   freely because consuming messages as work progresses cleans up the queue
   namespace automatically. Treating a SimpleBroker queue as an unconsumed,
   append-only relational table that requires tombstones and manual garbage
   collection violates the substrate model.

3. **Detached process topology bridged by task-owned runtime state (`weft.state.tasks.<tid>`)**:
   When task $A$ submits child task $B$, $A$ does not `fork` or `exec` $B$; $A$
   enqueues a spawn request in `weft.spawn.requests` and the Manager launches
   $B$ in an isolated `spawn`-context process. Furthermore, ordinary user tasks
   are launched detached from the Manager (`stop_with_parent=False`) so running
   tasks survive Manager drain, idle timeout, crash, or supersession. Because
   neither the submitting parent nor a replacement Manager holds the child in
   its OS `ppid` tree or `self._child_processes` map, `weft.state.tasks.<tid>`
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
     `ctrl_out`, and `weft.state.tasks.<tid>` publication.
   - **The operating system** reports process exit through kernel sentinels and
     scoped `(pid, create_time)` identity, never via application heartbeats.
   - **Higher-level intelligence** sits above Weft; Weft is the execution
     substrate, not the agent planner or policy engine.

## Core concepts and ownership [WT-3]

| Concept | Meaning | Owner | Exact contract owner |
|---------|---------|-------|----------------------|
| Task | One unit of work with a TID, a TaskSpec, and a per-task queue set | The task process | `01-Core_Components.md` [CC-2.1], [CC-2.2.1] |
| TID | A 64-bit SimpleBroker hybrid timestamp; the spawn-request message ID becomes the task's immutable TID | SimpleBroker mints the ID; Weft preserves it | `04-SimpleBroker_Integration.md` [SB-0.2]; `07-System_Invariants.md` IMMUT.3 |
| TaskSpec | Frozen execution specification (`spec`, `io`) plus forward-only runtime state (`state`) | The resolution and task lifecycle path | `02-TaskSpec.md` [TS-1]; IMMUT.1, IMMUT.2 |
| Lifecycle state | Forward-only state machine (`created` → `spawning` → `running` → terminal) | The task that publishes the transition | `07-System_Invariants.md` STATE.1, STATE.2 |
| Manager | A task that classifies requests, enforces measured admission and branch frontiers, reserves and spawns children, and reaps exits | The Manager process | `03-Manager_Architecture.md` [MA-1], [MA-1.8], [MA-3] |
| Branch frontier | A live finite ancestry path retained for child-first scheduling, without a family concurrency permit (LIFO descent, FIFO root entry) | The Manager process (checkpointed in `weft.state.tasks.<manager_tid>`) | `03-Manager_Architecture.md` [MA-1.8]; `2026-10-15-work-conserving-family-scheduling.md` |
| Pending family bucket | Consume-on-dispatch queue `weft.state.labels.<root_tid>.pending.<parent_tid>` indexing accepted child requests by parent; auto-disappears when emptied | The Manager process | `05-Message_Flow_and_State.md` [MF-5]; `2026-10-15-work-conserving-family-scheduling.md` |
| Reactor and wake arbiter | Single wait point (`PollingStrategy`) per task over a closed set of queue, event, and deadline inputs | The task driver thread | [CC-2.2.1]; `07-System_Invariants.md` IMPL.8 to IMPL.11 |
| Source adapter | Dedicated helper thread that owns one blocking OS or worker wait and wakes the reactor with one immutable result | The task that created it | [CC-2.2.1]; `2026-09-17-watcher-reactor-restoration-plan.md` |
| Control message | A request row on `T{target}.ctrl_in`; replies are pushed to the requester's own `T{requester}.ctrl_in` (`reply_to`) | The task reading `ctrl_in` | `05-Message_Flow_and_State.md` [MF-3]; `2026-09-18-event-routed-manager-pong-plan.md` |
| Completion fact | One serialized terminal record published atomically to `weft.log.tasks` and `T{tid}.ctrl_out` | The completing task | [MF-3], [MF-5]; `07-System_Invariants.md` OBS.1 |
| Runtime-state queue | `weft.state.*` ephemeral operational state (excluded from dumps, read via peek-latest; `weft.state.tasks.*` deleted only by `LivenessMonitor`) | The publishing task or service; `LivenessMonitor` for task-state deletion | `07-System_Invariants.md` OBS.6, LIVENESS.R3; `05-Message_Flow_and_State.md` [MF-5] |
| Broker session | Process-local lease over shared backend runner/pool resources with single-thread caller cache | The task or command that opened it | `04-SimpleBroker_Integration.md` [SB-0.4]; `01-Core_Components.md` [CC-2.2.1] |
| Admission | Measured gate on process creation (`available = C - R - U`), never on queue-first submission | The Manager | `03-Manager_Architecture.md` [MA-1.8]; `2026-10-15-work-conserving-family-scheduling.md` |
| Evidence | Queue row, scoped `(pid, create_time)` process identity, or runtime handle supporting a liveness or custody claim | The reader classifying it under liveness policy | [MF-5]; `07-System_Invariants.md` LIVENESS.R3–R7 |

## Design principles [WT-4]

The "Evidence" column names the owner session or plan in which the principle
was established. The "Contract" column names the governing specification or
engineering rule.

### 4.1 Ontology and Queue Mechanics

| Principle | Design consequence | Evidence | Contract |
|-----------|--------------------|----------|----------|
| Everything is a task | No caller gets a privileged side-channel. Even a short-lived CLI requester mints a TID and receives control replies on `T{tid}.ctrl_in`. | Owner, 2026-09-17 | [MF-3]; `2026-09-18` PONG plan |
| It is just a message | Protocols are ordinary messages on ordinary queues. Replies are pushed to a queue the requester's reactor already watches—push, never pull-scan. | Owner, 2026-09-17: "this should not be a special case. It is just a message." | [MF-3] |
| Queues carry facts; processes carry hints | No in-memory lifecycle database. Detached task lineage and process evidence live in `weft.state.tasks.<tid>`; local Manager tables are caches/hints rebuilt from queues. | Owner, 2026-09-23; `2026-10-12` burst plan | OBS.1, OBS.6; [MF-5] |
| Consume-to-disappear queues, not relational tables | SimpleBroker queues auto-create on write and auto-disappear when emptied. Consume tokens as work is dispatched (`delete`/`move_one`) instead of accumulating unconsumed event logs that require tombstones and manual GC sweeps. | Owner & architectural review, 2026-10-14 | [SB-0.1]; `2026-10-14` family scheduling plan |
| Address a fact to its observers | Terminal completion is broadcast atomically to `weft.log.tasks` and `T{tid}.ctrl_out` so task waiters watch only their target's queue instead of scanning global history in $O(W \cdot E)$. | `2026-10-12` burst plan; owner, 2026-10-09 | [MF-3], [MF-5], OBS.1 |
| Keep an identity you already have | An owner that wrote a row retains the returned ID; a caller that created a TaskSpec derives queue names from its TID rather than scanning logs to rediscover it. | Owner, 2026-10-09: "We don't need to re-discover it from the taskspec that we just created" | [MF-5]; [SB-0.2] |

### 4.2 Classical Algorithms and Resource Control

| Principle | Design consequence | Evidence | Contract |
|-----------|--------------------|----------|----------|
| Classical CS models over blunt ad-hoc fixes | Start from the well-studied CS formulation (reactor demultiplexing, work-conserving DFS/work-sharing, measured admission control, two-phase custody handoff) and realize it with existing primitives rather than inventing lanes, heuristics, or magic budgets. | Owner, 2026-10-14 | [MA-1.8]; `2026-10-14` family scheduling plan |
| Queued work costs nothing; live processes cost | Submission is always queue-first and unblocked. Admission gates process creation using measured pressure (`C - R - U`), never prospective per-task connection debt or predeclared child counts. | Owner, 2026-10-14: "Tasks sitting waiting to be spawned do not consume processes. Only live processes do" | [MF-1]; [MA-1.8] |
| Use the primitives you have | Compose labels, bounded queues, FIFO/LIFO reads, atomic exact-ID moves, and `weft.state.tasks.*` snapshots before proposing new infrastructure. | Owner, 2026-10-14; 2026-09-17: "We already have the primitives we need in the multiqueue watcher" | [SB-0.1], [SB-0.4] |
| Push a concern down when it belongs lower | Connection attribution belongs in SimpleBroker because SimpleBroker opens connections; admission and scheduling policy belong in Weft. SimpleBroker never depends on Weft. | Owner, 2026-10-06, 2026-10-13, 2026-09-17 | [SB-0.4] |
| Process concurrency, short transactions | Weft isolates work in OS processes (`spawn` context), not worker threads, and keeps broker transactions short. On PostgreSQL the scarce resource is connection count. | Owner, 2026-09-15 | [SB-0.4] |
| One owner for each resource | Each `BrokerSession`, queue lease, file descriptor, `weft.state.tasks.*` deleter (`LivenessMonitor`), and worker result has a single explicit owner. | Owner, 2026-09-15 | [SB-0.4]; [CC-2.2.1]; QUEUE.7, QUEUE.8; LIVENESS.R3 |

### 4.3 Reactor, Liveness, and Process Topology

| Principle | Design consequence | Evidence | Contract |
|-----------|--------------------|----------|----------|
| One reactor, one arbiter, a closed set of inputs | Every task waits at one `PollingStrategy` arbiter driven by backend hints/notifications, local `notify_activity()` events, OS sentinel adapters, and explicit deadlines—never defensive polling wait-caps. | Owner, 2026-09-17: "we are all event driven, with just a limited number of types of events" | [CC-2.2.1]; IMPL.8 to IMPL.11 |
| A quiet task does nothing | When no event arrives and no deadline is due, a reactor sleeps without running turns. First wake after quiet is bounded by one SimpleBroker pass (~100 ms), followed by burst mode. | Owner, 2026-09-17 | [CC-2.2.1] |
| Availability and death are different questions | A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death. Task-owned terminal proof wins over a Manager `wrapper_lost` record, and reservation/checkpoint custody survives until task-snapshot handoff or positive settlement. | Owner, 2026-09-08; `2026-09-17` discovery plan; `2026-10-14` family scheduling plan | [MA-1.6a], [MA-3], [MF-5], LIVENESS.R3–R7 |
| Ask who owns this lifetime | A shared Manager, a detached child task (`stop_with_parent=False`), and a waiting client have independent lifetimes. Manager supersession changes who takes future work; it never kills or moves running children. | Owner, 2026-09-08 | [MA-1], [MA-3]; `14-Python_API_Surfaces.md` [PY-2] |

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
| No compatibility code | Contract changes update all producers and consumers in one coordinated release with a bounded forward migration for durable queue data—no permanent dual decoders or silent fallback lanes. | Owner, 2026-09-17: "No need for backwards compat" | `00-Overview_and_Architecture.md`, Single current contract |
| One path on all platforms | Windows, macOS, and Linux share the same lifecycle and cleanup architecture; platform-specific branches are narrow adapter leaves. | Owner, 2026-10-07, 2026-09-09 | `2026-09-10` process-title plan |
| The client mirrors the CLI | The Python client and the CLI expose the same mental model and verbs over shared `weft/commands/` capabilities. | Owner, 2026-09-23 | `11-CLI_Architecture_Crosswalk.md`; [PY-1] |
| Specs are what is; theory is where we go | When a spec reflects a flawed design, revise the spec through a class-5 plan; plans record execution status, while specs state intended behavior. | Owner, 2026-10-09 | `docs/agent-context/decision-hierarchy.md` |

## What Weft is not [WT-5]

Weft is not a distributed workflow engine, a general DAG/join compiler, an
agent-management or prompt-orchestration layer, a provider-health monitor, or a
second control plane. It does not maintain a sidecar relational database of
task state, probe external tools before an ordinary `weft run`, or invent
application policy. See `00-Overview_and_Architecture.md`, Product Boundary.

Weft does not own queue mechanics. Message IDs, ordering, atomic moves,
delivery claims, and storage transactions belong to SimpleBroker. If a problem
requires a queue-level primitive, that primitive belongs in SimpleBroker.

Weft operates within a single user/operator trust domain. The operating system,
filesystem permissions, and database grants are the security boundary—not
in-band command sanitizers or authenticated control messages. The owner stated
on 2026-09-17 that a hand-crafted `PING` "is not going to be a threat we are
going to worry about".

## Negative knowledge: rejected alternatives [WT-6]

Each record keeps a decision that the owner made against a plausible
alternative. An agent must not propose the alternative again unless the
"Reconsider when" condition is true. Records use the SimpleBroker
`[ALT-*]` form.

### [ALT-WT-001] A child lane and per-task connection budgets

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

- Disposition: rejected
- Candidate: Wait caps of 50 ms and 25 ms on worker, signal, parent-loss, and
  PONG discovery, so a reactor that missed an event still made progress.
- Why plausible: Before the event-driven path worked, the caps hid the gap.
- Reason: The caps were "accumulated cruft" (owner, 2026-09-17). Each local
  source now calls `notify_activity()`. An ordinary task has no timer. A
  service task supplies its own deadline through `next_wait_timeout()`.
- Current consequence: The first wake after quiet is one SimpleBroker pass,
  about 100 ms. After that the strategy is in burst mode.
- Reconsider when: A local source exists that cannot call the arbiter and has
  no adapter. Then add the adapter, not a cap.
- Evidence: `2026-09-17-watcher-reactor-restoration-plan.md`; [CC-2.2.1].

### [ALT-WT-003] A PONG found by a poll of the target's `ctrl_out`

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
- Reconsider when: Never. A new control reply follows the same shape.
- Evidence: `2026-09-18-event-routed-manager-pong-plan.md`; [MF-3].

### [ALT-WT-004] A heartbeat as the TaskMonitor clock

- Disposition: rejected
- Candidate: The built-in heartbeat service sends a message on each interval,
  and TaskMonitor cycles when it receives one.
- Why plausible: A queue message is an event, and events drive reactors.
- Reason: TaskMonitor also kept a local deadline, so it had two clocks. A
  clock is a timer, not a message. Each service keeps its own clock through
  `next_wait_timeout()`. The heartbeat is an emitter for consumers that want
  one.
- Current consequence: The decision is made. The implementation is not. The
  plan is a draft. TaskMonitor still registers with the heartbeat
  (`weft/core/monitor/task_monitor.py`, `_heartbeat_registered`). The landed
  part is commit `da5fa0f6`: the Manager wants the heartbeat service only when
  TaskMonitor is enabled.
- Reconsider when: Never for a clock. A consumer that wants a periodic
  message can still register.
- Evidence: Owner decision, 2026-09-08; `2026-09-08-service-wake-model-and-heartbeat-decoupling-plan.md` (draft).

### [ALT-WT-005] A shallow copy of a task as a maintenance worker

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

### [ALT-WT-007] A one-way drain on supersession

- Disposition: rejected
- Candidate: When a Manager received a supersession notice, it entered a
  one-way drain, with persistent children, and never resumed dispatch.
- Why plausible: A superseded Manager must stop taking public work.
- Reason: The same duplicate-Manager condition had two different policies,
  one reversible and one not. The notice changes who takes future work. It
  does not move existing work. A Manager keeps its ordinary loop and makes
  one small decision about intake and exit.
- Current consequence: One convergence policy, reversible, for automatic
  supersession. Explicit operator replacement stays distinct and binding.
- Reconsider when: Never for automatic convergence.
- Evidence: Owner dialogue, 2026-09-08; [MA-1], [MA-3];
  `2026-05-20` lesson in `docs/lessons.md`.

### [ALT-WT-008] A Manager reuse flag

- Disposition: rejected, removal not yet landed
- Candidate: `WEFT_MANAGER_REUSE_ENABLED`, a configuration key that let a
  caller refuse an existing Manager.
- Why plausible: Tests wanted a fresh Manager.
- Reason: The flag is test-only and inert after the RunSession migration. A
  Manager belongs to the context, not to the invocation that started it.
- Current consequence: The constant is still in `weft/_constants.py` and is
  always `True`. The retirement plan is a draft. `weft run` finds a Manager
  or starts one.
- Reconsider when: Never.
- Evidence: Owner decision, 2026-09-08;
  `2026-09-08-manager-reuse-flag-retirement-and-run-lifecycle-plan.md` (draft).

### [ALT-WT-009] Every waiter scans the shared task log for completion

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

- Disposition: rejected
- Candidate: A general subsystem to make repeated discovery cheap.
- Why plausible: An index or a cache removes repeated work.
- Reason: A cache that becomes startup truth is a second lifecycle database.
  The fixes were applications of the existing theory, not new machinery.
- Current consequence: No heaps, counters, or registries exist only to claim
  constant cost. In-memory state stays a hint.
- Reconsider when: A measured cost remains after every existing primitive is
  used correctly, and the owner accepts a named new concept.
- Evidence: `2026-10-12-burst-submission-complexity-plan.md`, invariant 8.

### [ALT-WT-011] Hidden preflight on the ordinary execution path

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

- Disposition: rejected
- Candidate: A `validate_safe_path` style helper that rejects dangerous paths.
- Why plausible: Shell injection is a common risk.
- Reason: There is no shell. Commands are argv lists. A sanitizer implies a
  boundary that does not exist.
- Current consequence: Validate at the boundary once, then stay strict.
- Reconsider when: Weft stops using argv-only execution.
- Evidence: `CLAUDE.md` section 4.11.

### [ALT-WT-015] Manager-local child tracking or OS `ppid` as the liveness/lineage source

- Disposition: rejected
- Candidate: Use the Manager's in-memory `self._child_processes` table or OS
  parent-PID trees to decide whether a task is alive and which branch it
  belongs to.
- Why plausible: The Manager started the child and holds its `BaseProcess`
  handle while the Manager remains alive.
- Reason: Submitted children are never forked by their submitting parent task,
  and ordinary user tasks are launched with `stop_with_parent=False` so they
  survive Manager drain, idle exit, crash, or replacement by design ([MA-1],
  [MA-3]). The Manager's in-memory table is only a local hint. Task-owned
  `weft.state.tasks.<tid>` snapshots (with pre-handoff custody in
  `T{manager}.reserved` + `weft.state.tasks.<manager_tid>`) are the durable
  bridge between scoped `(pid, create_time)` process identity and task lineage
  (`root_tid`, `submitter_tid`, `ancestry_tids`, `scheduling_class`).
- Current consequence: Task status and recovery read `weft.state.tasks.*`
  (`weft/commands/tasks.py`, `read_task_state_snapshot`). A terminal event
  alone never proves process exit, and `LivenessMonitor` is the sole component
  deleter of `weft.state.tasks.*`.
- Reconsider when: Never.
- Evidence: Owner, 2026-09-23 and 2026-10-14; `07-System_Invariants.md`
  OBS.6, LIVENESS.R3–R7.

### [ALT-WT-016] Re-derive the task graph on each dispatch or bulk-move pending tokens on parent exit

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

## Tensions and falsifiers [WT-7]

- **Possession probe:** One time for each release or class-5 plan, ask an
  agent to place a hypothetical feature, refuse a category error, and
  predict the class of bug an audit will find. Record the result in the
  plan or in the lessons. A failed probe means the account or its
  possession has drifted.
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
  compose as tasks and queues. If a feature requires a second control plane or
  a sidecar database, the theory or the feature is wrong.
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
  enough. Sharpen the theory rather than accepting the proposal.
- **The "expert Python developer" test vs. measured cost:** When naive
  simplicity causes $O(W \cdot E)$ or $O(N^2)$ burst amplification, resolve the
  tension by choosing the right algorithm (atomic observer broadcast, $O(1)$
  consume-on-dispatch buckets) while keeping one durable source of truth.

## Founding continuity [WT-8]

The first agent file in this repository, commit `4ef911ea` (2026-02-10),
stated the model in one line: "Everything is a Task. The Manager is a Task
that spawns other Tasks. Queues are the only communication channel. State
lives in queues, not databases." Every principle in [WT-4] is an
application of that line.

| Current principle | Founding text | Maintained | Evolved |
|-------------------|---------------|------------|---------|
| Everything is a task | "Everything is a Task" | Still the first rule | Agents, pipelines, services, and short-lived requesters are tasks too |
| Queues carry facts; consumed queues disappear | "State lives in queues, not databases" | Still the only durable record | Runtime-state queues (`weft.state.tasks.*`) hold detached process/lineage snapshots deleted by `LivenessMonitor`; pending family queues (`weft.state.labels.*.pending.*`) are consumed on dispatch so they vanish automatically; completion facts broadcast to `ctrl_out` |
| Specs are the contract | "The specs are the source of truth. Code must align to them." | Still the contract | The theory now sits above the specs for design judgment; a wrong spec changes |
| Small observable substrate | "the durable task substrate for agent systems" (`CLAUDE.md`) | Still the product boundary | Measured admission, ancestry scheduling, labels, and sessions were added as classical algorithms over existing primitives without a second control plane |

## Revisions [WT-9]

Revision records put the current account first so historical material serves as
evidence of why the theory evolved rather than a competing model.

### [REV-WT-001] Single-arbiter event reactor replaced defensive polling wait-caps

- Current account: Every task is an event-driven reactor waiting on one
  SimpleBroker `PollingStrategy` arbiter over a closed set of inputs (backend
  queue notifications/hints, local `notify_activity()` completions, OS sentinel
  adapters, and explicit service deadlines). A quiet task does zero turns and
  uses no defensive 25 ms/50 ms polling caps.
- Supersedes: Earlier watcher and control paths clamped wait timeouts with
  short 25 ms and 50 ms polling caps so missed local transitions would still be
  noticed on the next tick.
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

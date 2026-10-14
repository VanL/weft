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

This document uses ASD-STE100 Simplified Technical English. Sentences are
short and in the active voice. The words "must" and "must not" give a
requirement. The word "can" gives a possibility. Quotations from the owner
keep their original words.

## Technical names

These words are technical names in this document. They have the meaning
that the Weft specifications give them.

- queue, inbox, outbox, reserved queue, `ctrl_in`, `ctrl_out`, task log
- task, TID, TaskSpec, Manager, Consumer, service task, pipeline, agent
- reactor, wake arbiter, turn, timer, hint, event, source adapter
- owner, custody, lease, session, broker target, backend
- evidence, terminal, liveness, supersession, admission, reap, sentinel
- runtime-state queue (`weft.state.*`), family, label
- durable, hybrid timestamp, spawn request

## What "program theory" means [WT-0]

Weft uses the meaning of "theory" from Peter Naur, "Programming as Theory
Building" (1985).[^naur] A program theory is not a formal theory and not a
list of requirements. It is the working model that connects the problem to
the concepts, the owners, and the limits of the program.

A person has the theory when the person can do four things:

- connect a problem in the world to the shape of the program
- explain why the shape is what it is
- add a new demand and keep the program coherent
- find the place for a proposal, and refuse a proposal that does not fit

A person who can only repeat this document does not have the theory.

The owner gave this theory a name on 2026-09-08: "weftness". The owner
defined it as "the conceptual core of the program". SimpleBroker has the
same type of document, `../simplebroker/docs/program-theory.md`, and the
owner uses "simplebrokerness" and "tautness" for the sibling projects.

Each surface answers one question:

| Surface | Question it answers |
|---------|---------------------|
| Program theory (this file) | What kind of system is Weft? Why do its concepts and limits exist? |
| Specifications (`docs/specifications/`) | What is the exact intended behavior now? |
| Plans (`docs/plans/`) | What change was made, under what evidence and premises? |
| Code and tests | How is the behavior realized? What evidence fires against each claim? |
| Lessons (`docs/lessons.md`) | What correction did a repeated failure cause? |

The owner gave the rule for the first two rows on 2026-10-09: "The specs
describe what we have. The theory describes what we are building toward.
We are not trying to reify bad decisions." If a specification is wrong, the
specification changes. The theory does not bend to it.

[^naur]: Peter Naur, "Programming as Theory Building" (1985); reprinted in
    *Computing: A Human Activity* (1992).

## Purpose and desired feel [WT-1]

Weft is "the durable task substrate for agent systems". It runs commands,
Python callables, and agent-backed work that can take milliseconds or
minutes. The work can outlive the shell session that started it.

Weft must feel like a small set of Unix tools, not like a platform. A user
can see all of its state with `ps` and a queue peek. Failures stay visible.
The system does nothing in the background that the project did not ask for.

"Simple" means a small number of concepts at the use surface. It does not
mean a small number of source lines. A large file is correct when its parts
share state and must change together. See `CLAUDE.md` section 1.1.

## Whole-program mental model [WT-2]

Everything is a task. A Manager is a task that starts other tasks. An agent
is a task. A dangerous command in a container is a task. The owner stated
the rule on 2026-09-17: "In weft, everything is a task" and "There is no
code without a task identity."

Queues are the only durable record of a fact. If a fact about a task
lifecycle is true, it is true because a row is in a queue. A process can
hold a copy of a fact. That copy is a hint or an owned process handle. It
is never a second lifecycle database.

Each task is a reactor. One wake arbiter, the SimpleBroker `PollingStrategy`,
waits for a small, closed set of inputs. On SQLite the backend input is the
`data_version` hint, confirmed against real queue state. On PostgreSQL the
backend input is the listener for this task's queues. Local events and
timers enter the same arbiter. If no input arrives and no timer is due, the
task has nothing to do, and it must not run a turn.

Ownership is explicit at every level:

- SimpleBroker owns queue operations, message identity, delivery, and
  transactions. Weft does not re-implement them.
- The backend owns storage.
- The Manager owns the start of tasks, admission, and child supervision.
- A task owns its inbox, outbox, reserved queue, `ctrl_in`, and `ctrl_out`.
  Nobody else speaks for the task.
- The operating system reports child exit. Weft uses sentinels and
  `multiprocessing.connection.wait`, not a heartbeat.

Higher-level intelligence is above Weft. Weft makes execution reliable,
visible, and easy to validate. It does not manage the agents, the
providers, or the policy of the system that embeds it.

## Core concepts and ownership [WT-3]

| Concept | Meaning | Owner | Exact contract owner |
|---------|---------|-------|----------------------|
| Task | One unit of work with a TID, a TaskSpec, and a set of queues | The task process | `01-Core_Components.md` [CC-2.1], [CC-2.2.1] |
| TID | A 64-bit SimpleBroker hybrid timestamp. The spawn-request message ID becomes the TID | SimpleBroker gives the ID; Weft keeps it | `04-SimpleBroker_Integration.md` [SB-0.2]; `07-System_Invariants.md` IMMUT.3 |
| TaskSpec | The frozen execution configuration plus mutable runtime state | The resolution path | `02-TaskSpec.md` [TS-1]; IMMUT.1, IMMUT.2 |
| Lifecycle state | A forward-only state machine | The task that publishes the fact | `07-System_Invariants.md` STATE.1, STATE.2 |
| Manager | A task that reserves spawn requests, starts children, and supervises them | The Manager process | `03-Manager_Architecture.md` [MA-1], [MA-1.6a], [MA-3] |
| Reactor and wake arbiter | One wait point for each task, with a closed set of inputs | The task driver thread | [CC-2.2.1]; `07-System_Invariants.md` IMPL.8 to IMPL.11 |
| Source adapter | A thread that owns one blocking operation and publishes one immutable result | The task that created it | [CC-2.2.1]; `2026-09-17-watcher-reactor-restoration-plan.md` |
| Control message | A row on `ctrl_in`; a reply is a row on the requester's own `ctrl_in` | The task that reads the queue | `05-Message_Flow_and_State.md` [MF-3]; `2026-09-18-event-routed-manager-pong-plan.md` |
| Completion fact | One serialized record, published to the task log and to `ctrl_out` in one atomic broadcast | The task | [MF-3], [MF-5]; `07-System_Invariants.md` OBS.1 |
| Runtime-state queue | `weft.state.*`, runtime only, not in dumps, read with peek-latest | The service that writes it | `07-System_Invariants.md` OBS.6; `05-Message_Flow_and_State.md` [MF-5] |
| Broker session | A lease on the process-shared backend resources, with one owner thread | The task or command that opened it | `04-SimpleBroker_Integration.md` [SB-0.4]; `01-Core_Components.md` [CC-2.2.1] |
| Admission | A measured gate on process creation, never on submission | The Manager | `03-Manager_Architecture.md` [MA-1.8]; `2026-10-14-measured-admission-family-scheduling.md` (draft) |
| Evidence | A queue row, a process identity, or a runtime handle that supports one claim about a task | The reader that classifies it | [MF-5]; `01-Core_Components.md` LIVENESS.R3 |

## Design principles [WT-4]

The "Evidence" column names the owner session or document in which the
owner stated the principle. The "Contract" column names where the current
specifications hold it. A principle with no contract row lives only in this
document and in plan prose.

| Principle | Design consequence | Evidence | Contract |
|-----------|--------------------|----------|----------|
| Everything is a task | No caller gets a special path. A short-lived requester mints a TID and uses `T{tid}.ctrl_in`. | Owner, 2026-09-17 | [MF-3]; `2026-09-18` PONG plan |
| It is just a message | A protocol is ordinary rows on ordinary queues. The only special code recognizes the message at each end. Replies go to a queue the requester already watches. Push, not pull. | Owner, 2026-09-17: "this should not be a special case. It is just a message." | [MF-3] |
| One reactor, one arbiter, a closed set of inputs | Inputs are a backend hint, a local event, a timer, or an operating-system source through an adapter. A timer exists only for clock work or for absence. No wait caps. | Owner, 2026-09-17: "we are all event driven, with just a limited number of types of events" | [CC-2.2.1]; IMPL.8 to IMPL.11 |
| A quiet task does nothing | A wait of one second or five seconds with no work is correct. The first wake after quiet is in the SimpleBroker bound, about 100 ms. | Owner, 2026-09-17 | [CC-2.2.1] |
| Queues carry facts; processes carry hints | No in-memory lifecycle database. Runtime state goes in `weft.state.*` and holds only what is necessary. | Owner, 2026-09-23; `2026-10-12` burst plan | OBS.1, OBS.6; [MF-5] |
| Address a fact to its observers | A completion fact goes to `ctrl_out` in the same write as the task log. A waiter does not scan everybody's history. | `2026-10-12` burst plan; owner, 2026-10-09 | [MF-3], [MF-5], OBS.1 |
| Keep an identity you already have | An owner that wrote a row keeps the returned ID. A caller that made a TaskSpec does not rediscover it from the log. Queue names follow from the TID. | Owner, 2026-10-09: "We don't need to re-discover it from the taskspec that we just created" | [MF-5]; [SB-0.2] |
| Shared function, shared code | Two code paths for one function is an error. A new subclass must show why the shared code cannot serve. | Owner, 2026-10-09 and 2026-09-21 | Engineering principles 1 and 4.2 |
| As few concepts as possible | A change consolidates. It does not add a lane, a store, a bus, a scheduler, or a cache subsystem. | Owner, 2026-09-21: "We want to have as few concepts as possible"; 2026-10-14 | `00-Overview_and_Architecture.md`, Product Boundary |
| No over-armoring | Code is correct for real use paths, not for failures that do not occur. A best-effort path tries, then catches and reports. An unreasonable case gets a sentence in the documentation, not a mechanism. | Owner, 2026-09-07, 2026-09-15, 2026-10-13 | Engineering principles, Secondary Rules |
| Availability and death are different questions | A missing registry row, an expired window, or a failed read is "unknown", not death. Task-owned terminal proof wins over a Manager `wrapper_lost` record. | Owner, 2026-09-08; `2026-09-17` discovery plan | [MA-1.6a], [MA-3], [MF-5] |
| Ask who owns this lifetime | A shared Manager, a submitted task, and a client that watches the task have different owners and different reasons to stop. Supersession changes who takes future work. It does not move existing work. | Owner, 2026-09-08 | [MA-1], [MA-3]; `14-Python_API_Surfaces.md` [PY-2] |
| One owner for each resource | A session, a queue lease, a file descriptor, and a worker result each have one owner. A user of the API gets a connection through the API and never sees the pool. | Owner, 2026-09-15 | [SB-0.4]; [CC-2.2.1]; QUEUE.7, QUEUE.8 |
| Process concurrency, short transactions | Weft uses processes, not threads, for concurrency. Every broker transaction is short. When a pool is full, callers wait and make progress. On PostgreSQL the limit that matters is the connection count. | Owner, 2026-09-15 | [SB-0.4] |
| Queued work costs nothing; live processes cost | Admission limits process creation. Submission is queue-first and never blocked. A task does not tell the Manager how many children it will make. | Owner, 2026-10-14: "Tasks sitting waiting to be spawned do not consume processes. Only live processes do" | [MF-1]; [MA-1.8] |
| Use the primitives you have | Labels, bounded queues, FIFO and LIFO order, exact-ID moves, and runtime-state queues are the building blocks. Well-known computer science answers apply. | Owner, 2026-10-14; 2026-09-17: "We already have the primitives we need in the multiqueue watcher" | [SB-0.1], [SB-0.4] |
| Push a concern down when it belongs lower | Connection attribution lives in SimpleBroker because SimpleBroker opens the connections. Admission policy lives in Weft. SimpleBroker never waits for Weft. | Owner, 2026-10-06, 2026-10-13, 2026-09-17 | [SB-0.4] |
| Measure; do not bake in inefficiency | A performance claim needs a measurement on SQLite and on PostgreSQL. A fixed small deadline is suspect until measured. | Owner, 2026-09-11, 2026-09-17, 2026-10-09 | `2026-10-12` burst plan, final evidence |
| Tests prove correctness, nothing else | A test that pins a version, a process step, or an implementation detail is suspect. Green tests after a cleanup prove nothing by themselves. Reduced test concurrency is never a gate. | Owner, 2026-09-16, 2026-10-06, 2026-09-10, 2026-09-15 | `08-Testing_Strategy.md`; engineering principle 4 |
| The expert Python developer test | A design must be one that an expert Python developer calls correct, simple, and elegant for a system with these principles. A normally constructed object with explicit inputs beats a copied and reset task. | Owner, 2026-10-09, 2026-10-14, 2026-09-21 | `2026-09-22` MaintenanceWorker plan |
| No capability diminution | A simplification must not remove an observable function or add latency. Each plan keeps a difference register. Added latency counts as a diminution. | Owner, 2026-09-07; review rule in the 2026-09-08 plans | Hardening runbook |
| No compatibility code | A contract change updates all producers and consumers in one release. No legacy decoders. The CHANGELOG records the break. | Owner, 2026-09-17: "No need for backwards compat" | `00-Overview_and_Architecture.md`, Single current contract |
| One path on all platforms | Windows, macOS, and Linux use the same cleanup and lifecycle path. Platform code is the exception, not the rule. | Owner, 2026-10-07, 2026-09-09 | `2026-09-10` process-title plan |
| The client mirrors the CLI | The Python surface and the CLI express one model. Stable verbs stay at the center. | Owner, 2026-09-23 | `11-CLI_Architecture_Crosswalk.md`; [PY-1] |
| Specs are what is; theory is where we go | A wrong specification changes. A plan records implementation status. The specification does not. | Owner, 2026-10-09 | `docs/agent-context/decision-hierarchy.md` |

## What Weft is not [WT-5]

Weft is not a workflow engine, a job platform, an agent-management layer, a
provider-health system, or a second control plane. It does not keep a
separate database of task state. It does not probe the environment before
an ordinary `weft run`. It does not author project policy by itself. See
`00-Overview_and_Architecture.md`, Product Boundary.

Weft does not own queue mechanics. It does not own message IDs, ordering,
delivery, or transactions. Those belong to SimpleBroker. If a fix seems to
need a queue-level change, the fix belongs in SimpleBroker.

Weft assumes user-level trust. It is not a hostile multi-tenant system. The
operating system and the filesystem are the security boundary, not a Weft
input filter. The owner stated on 2026-09-17 that a hand-made PING "is not
going to be a threat we are going to worry about".

## Negative knowledge: rejected alternatives [WT-6]

Each record keeps a decision that the owner made against a plausible
alternative. An agent must not propose the alternative again unless the
"Reconsider when" condition is true. Records use the SimpleBroker
`[ALT-*]` form.

### [ALT-WT-001] A child lane and per-task connection budgets

- Disposition: rejected
- Candidate: A separate `weft.spawn.children` queue, a fixed connection charge
  for each new task (eight slots for `weft run ls`), and lifetime connection
  debt.
- Why plausible: A large burst exhausted PostgreSQL connections. Children were
  queued behind all parents in one FIFO. A second lane and a reservation
  looked like a direct fix.
- Reason: The lane was a special case. The charge was a guess, not a
  measurement. No child tasks started. The owner, 2026-10-14: "this creates a
  special case when we really should not have one. What we need is better
  admission control over our existing primary lane, not adding a new lane."
- Current consequence: The decision is made. The implementation is not. Release
  0.9.110 still ships `weft.spawn.children` (`weft/_constants.py`,
  `WEFT_CHILD_SPAWN_REQUESTS_QUEUE`) and the budget arithmetic. The intended
  state is: ordinary work stays on `weft.spawn.requests`; admission is
  `available = ceiling - reserve - measured use`; family order uses
  `weft.state.labels.*` and bounded FIFO/LIFO selection.
- Reconsider when: Never as a second public lane. Reconsider the selection
  rule if measured admission cannot keep a known workload in progress.
- Evidence: `2026-10-13-connection-aware-child-admission-plan.md` (superseded);
  `2026-10-14-measured-admission-family-scheduling.md`.

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

### [ALT-WT-015] Manager-local child tracking as the liveness source

- Disposition: rejected
- Candidate: The Manager's in-memory child table decides whether a task is
  alive.
- Why plausible: The Manager started the child and holds its handle.
- Reason: Children survive Manager death by design. The in-memory table is a
  hint. TID mappings and runtime handles, now `weft.state.tasks.*`, are the
  liveness source.
- Current consequence: Task status reads `weft.state.tasks.<tid>`
  (`weft/commands/tasks.py`, `read_task_state_snapshot`). A projection of
  status and list from only those queues is a draft plan
  (`2026-09-23-task-state-current-view-plan.md`). A terminal event alone
  never proves process exit.
- Reconsider when: Never.
- Evidence: Owner, 2026-09-23; `01-Core_Components.md` LIVENESS.R3.

### [ALT-WT-016] Re-derive the task graph on each dispatch

- Disposition: rejected
- Candidate: The Manager reads all TaskSpecs and reconstructs family
  ancestry each time it selects the next launch.
- Why plausible: The information is in the queues.
- Reason: "Sounds like a lot of re-work" (owner, 2026-10-14). Family
  membership changes incrementally, so the record must change incrementally.
- Current consequence: `weft.state.labels.*` holds incremental family
  membership and pending-request indexes (draft plan).
- Reconsider when: Never for each dispatch. A full reconciliation can run for
  final projection retirement.
- Evidence: `2026-10-14-measured-admission-family-scheduling.md`.

### [ALT-WT-017] Reduced test concurrency as an acceptance gate

- Disposition: rejected
- Candidate: Run the suite with `-n 0` or a low worker count to get a green
  result.
- Why plausible: Load causes some failures.
- Reason: "If load causes failures, that is in itself a signal of problems"
  (owner, 2026-09-09). A low-concurrency run hides the signal.
- Current consequence: All acceptance runs use the standard concurrency. A
  lower setting is for debugging one issue only.
- Reconsider when: Never.
- Evidence: Owner, 2026-09-15; `docs/lessons.md`.

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

## Tensions and falsifiers [WT-7]

- Possession probe. One time for each release or class-5 plan, ask an
  agent to place a hypothetical feature, refuse a category error, and
  predict the class of bug an audit will find. Record the result in the
  plan or in the lessons. A failed probe means the account or its
  possession has drifted.
- Simplicity against ambition. Weft adds agent runtimes, restricted
  runners, pipelines, and admission policy. Each addition must still look
  like a task. If a feature needs a second control plane, the theory or the
  feature is wrong.
- Event-driven against absence. Timers exist only for clock work or for
  absence. Each new timer is a question: which event cannot arrive?
- Measured admission is a soft guard. PostgreSQL enforces its own limit.
  External clients share the server. The theory does not promise exact
  per-task connection counts.
- A repeated use of private internals by an embedder (Engram, taut,
  weft_django) is evidence that a public seam is missing, not evidence that
  the embedder is wrong.
- A repeated proposal for a lane, a cache, a flag, or a shim is evidence
  that a principle in [WT-4] is not clear enough to agents. Tighten the
  principle; do not accept the proposal.
- The "expert Python developer" test and the "as few concepts as possible"
  rule can conflict with a measured cost. The burst plan shows the
  resolution: pay a fixed cost to remove a repeated cost, and keep one
  durable fact.

## Founding continuity [WT-8]

The first agent file in this repository, commit `4ef911ea` (2026-02-10),
stated the model in one line: "Everything is a Task. The Manager is a Task
that spawns other Tasks. Queues are the only communication channel. State
lives in queues, not databases." Every principle in [WT-4] is an
application of that line.

| Current principle | Founding text | Maintained | Evolved |
|-------------------|---------------|------------|---------|
| Everything is a task | "Everything is a Task" | Still the first rule | Agents, pipelines, services, and short-lived requesters are tasks too |
| Queues carry facts | "State lives in queues, not databases" | Still the only durable record | Runtime-state queues (`weft.state.*`) hold liveness and bookkeeping; a completion fact is addressed to its observers |
| Specs are the contract | "The specs are the source of truth. Code must align to them." | Still the contract | The theory now sits above the specs for design judgment; a wrong spec changes |
| Small observable substrate | "the durable task substrate for agent systems" (`CLAUDE.md`) | Still the product boundary | Admission, labels, and sessions were added without a second control plane |

## Revisions [WT-9]

None. This is the first draft. A later revision adds a `[REV-WT-nnn]`
record with the current account first and the superseded account as
evidence.

## Sources

- Owner sessions with Codex, 2026-09-01 to 2026-10-14, in the weft and
  simplebroker working directories. Dates in this document name the
  session in which the owner stated the rule.
- `docs/plans/2026-10-12-burst-submission-complexity-plan.md`, the first
  written use of "weftness".
- `docs/plans/2026-09-17-watcher-reactor-restoration-plan.md`, Design Rule.
- `docs/plans/2026-09-18-event-routed-manager-pong-plan.md`.
- `docs/plans/2026-09-22-task-monitor-maintenance-worker-plan.md`.
- `docs/plans/2026-10-14-measured-admission-family-scheduling.md` (draft).
- `../simplebroker/docs/program-theory.md`, the model for this document.
- `../agent-theory/docs/program-theory.md`, the process model.

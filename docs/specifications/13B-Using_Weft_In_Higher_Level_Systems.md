# Using Weft In Higher-Level Systems

This companion document captures exploratory patterns for using Weft as the
substrate inside a larger agent system.

It is not current contract. It is not promised core Weft product surface. It
exists to make the layering explicit when a higher-level system wants to build
on top of Weft's task, runner, queue, agent, and pipeline model.

Canonical behavior lives in the current specs:

- [00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)
- [07-System_Invariants.md](07-System_Invariants.md)
- [12-Pipeline_Composition_and_UX.md](12-Pipeline_Composition_and_UX.md)
- [13-Agent_Runtime.md](13-Agent_Runtime.md)

If a pattern in this document hardens into a stable product promise, move it to
the canonical spec that owns that behavior.

Examples here may be relevant to a higher-level governance system, but this
document does not define that system's policy.

## 1. Purpose [AR-B0]

Some systems need more than a task runner. They also need:

- a public operator workflow
- durable domain-specific threads or cases
- resolver logic that decides what context to load first
- domain-specific action policy and approvals
- system-of-record state outside the agent runtime

Weft can support those systems well without becoming that system itself.

The mental model is:

- Weft runs work durably
- the higher-level system decides what work to run and what it means
- Weft stays the substrate, not the policy engine

## 2. Ownership Split [AR-B1]

### 2.1 What Weft should own

When a larger system uses Weft, Weft should remain responsible for:

- task identity and lifecycle
- queue-visible control and state
- runner selection and isolation
- resource limits
- explicit tool and environment boundaries
- execution artifacts and metadata emitted by the task runtime
- agent execution as one more task target
- pipeline execution as one more task target

This keeps the substrate stable. It also means agent and pipeline work stays
observable, controllable, and composable in the same way as any other task.

### 2.2 What the larger system should own

The larger system should normally own:

- operator-facing workflow and UX
- domain-specific truth models such as cases, reports, and deliveries
- approval policy and action policy
- resolver logic that chooses which domain context to load
- how to interpret or merge results back into domain truth
- long-lived public conversation, thread, or case semantics
- how Weft task IDs, pipeline IDs, and artifacts are linked into that domain
  record

This split matters because it prevents Weft from growing a second hidden truth
lane.

## 3. Common Usage Patterns [AR-B2]

### 3.1 Bounded interpretation tasks

A larger system can use Weft for bounded interpretation work by submitting
ordinary one-shot agent tasks.

Good fits:

- explain a case
- summarize a report
- review a patch
- prepare a grounded answer from cited inputs

The higher-level system supplies the input envelope and consumes the result.
Weft supplies durability, isolation, and control. This is a pattern, not a new
contract surface.

### 3.2 Supervisor or review tasks

A system can also use Weft for supervisor-style work that reviews the output of
other tasks or agents.

The important shape is still the same:

- the supervisor is just another task
- coordination still happens through queues, artifacts, or explicit task
  submission
- no hidden nested chat lane is required

### 3.3 Operator-facing delegated agents

A larger system may want an operator-facing general agent that lasts longer than
one prompt.

The clean Weft shape is:

- host that agent as a persistent Weft task
- use Weft runners to constrain the environment
- keep task control, stop, kill, timeout, and observability on the Weft side
- let the higher-level system own operator identity, UX, and approval policy

This uses current agent-runtime capability, but it is still a layering choice:
the higher-level system remains the owner of public workflow and domain state.

## 4. Session And Memory Patterns [AR-B3]

If a larger system needs durable public thread or case identity, it should not
assume Weft already owns that model.

The safer pattern today is:

- the higher-level system owns the public thread, case, or session identifier
- Weft task IDs, pipeline IDs, and task artifacts are linked into that higher-
  level record
- provider-native session identifiers are runtime metadata, not the system of
  record

If the delegated runtime loses its native session, the higher-level system can
still recover by rehydrating a new task from its own durable record plus Weft
artifacts and task history.

This is the right layer split for now. If Weft later standardizes a narrower
session substrate, that should be added explicitly to the canonical or planned
runtime docs.

## 5. Resolver And Tool Patterns [AR-B4]

Resolvers and tool profiles solve different problems and should usually live at
different layers.

- A resolver is higher-layer logic that decides what context to load first for
  a given domain question or workflow.
- A tool profile is substrate execution policy that decides what machine powers
  the task may use.
- A pipeline is composition policy for durable task chains.

That means a larger system may:

- choose a resolver before submitting the task
- materialize the resolved context into task input, attachments, or artifact
  references
- select a Weft tool or environment profile that matches the intended lane
- route composed work through a Weft pipeline when the work is a sequence of
  durable task steps

This keeps domain reasoning separate from execution policy.

## 6. Other Agents As Tasks [AR-B5]

A larger system may want one agent to call another. The preferred Weft shape is
not opaque nested chats. It is explicit task composition.

Preferred patterns:

- spawn a bounded sub-agent as another Weft task
- pass structured input or artifact references
- return structured output, citations, or artifact references
- use a Weft pipeline when the handoff is a durable multi-step chain
- keep lifecycle and audit on the Weft side

This fits Weft's "everything is a task" model and avoids hidden agent topology.

## 7. Read, Write, And Action Boundaries [AR-B6]

For operator-facing lanes, the default safe shape is:

- read domain truth
- produce append-only interpretation or recommendation outputs
- request explicit deterministic action flows when a write is needed

The higher-level system should own:

- who may request action
- what approvals are needed
- how deterministic writes occur
- how resulting state changes are recorded

Weft may host the relevant tasks. It should not silently become the domain
policy engine.

## 8. Example Mapping For A Larger System [AR-B7]

For a governance-style system, a clean layering looks like this:

- bounded interpretation agents can use the current `llm` runtime where that is
  enough
- supervisor work can use current `llm` or delegated one-shot runtimes,
  depending on tool depth
- an operator-facing delegated lane can run as a persistent Weft agent task in
  a restricted runner
- durable multi-step work can use a Weft pipeline instead of ad hoc chaining
- domain-specific case state, approvals, and action policy stay above Weft

This gives the larger system a durable substrate without forcing Weft to become
the whole product.

## Backlinks

- Current core agent-runtime contract:
  [13-Agent_Runtime.md](13-Agent_Runtime.md)
- Current pipeline composition contract:
  [12-Pipeline_Composition_and_UX.md](12-Pipeline_Composition_and_UX.md)
- Proposed Django-specific companion:
  [13C-Using_Weft_With_Django.md](13C-Using_Weft_With_Django.md)
- Planned substrate expansion:
  [13A-Agent_Runtime_Planned.md](13A-Agent_Runtime_Planned.md)

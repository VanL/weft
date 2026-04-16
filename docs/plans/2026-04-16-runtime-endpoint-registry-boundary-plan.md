# Runtime Endpoint Registry Boundary Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Add one small runtime primitive for stable project-local task names without
turning Weft into a service framework. The core slice is a runtime-only named
endpoint registry that resolves a stable name to an ordinary live task's queue
endpoints. Optional helper surfaces may make that primitive easier to use, but
task payload contracts, handoff policy, and durable domain state must stay
outside the core runtime contract.

Definition of done for the future implementation slice:

- Weft can resolve a stable project-local endpoint name to one canonical live
  task endpoint record.
- The registry remains runtime-only and queue-backed.
- Duplicate-name handling is deterministic and observable.
- CLI ergonomics stay thin and queue-shaped.
- Any service-style builtin stays an ordinary task and does not rely on hidden
  broker-specific storage.

## 2. Source Documents

Read these before implementing:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/agent-context/principles.md`](../agent-context/principles.md)
3. [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
4. [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
5. [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
6. [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
7. [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2.2], [CC-2.4]
8. [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-0], [MA-1], [MA-4]
9. [`docs/specifications/04-SimpleBroker_Integration.md`](../specifications/04-SimpleBroker_Integration.md) [SB-0.1], [SB-0.3], [SB-0.4]
10. [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-3], [MF-5], [MF-6]
11. [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [OBS.1], [OBS.2], [OBS.3], [MANAGER.1], [MANAGER.3]
12. [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-4]
13. [`docs/specifications/13B-Using_Weft_In_Higher_Level_Systems.md`](../specifications/13B-Using_Weft_In_Higher_Level_Systems.md) [AR-B1], [AR-B5]
14. [`docs/specifications/01A-Core_Components_Planned.md`](../specifications/01A-Core_Components_Planned.md) [01A-1]
15. [`docs/specifications/03A-Manager_Architecture_Planned.md`](../specifications/03A-Manager_Architecture_Planned.md) [03A-1]
16. [`docs/specifications/04A-SimpleBroker_Integration_Planned.md`](../specifications/04A-SimpleBroker_Integration_Planned.md) [04A-2.1]
17. [`docs/specifications/05A-Message_Flow_and_State_Planned.md`](../specifications/05A-Message_Flow_and_State_Planned.md) [05A-1]
18. [`docs/specifications/10A-CLI_Interface_Planned.md`](../specifications/10A-CLI_Interface_Planned.md) [10A-2.1]
19. [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)

## 3. Context and Key Files

Files to modify for the future implementation:

- `weft/_constants.py`
- `weft/commands/queue.py`
- `weft/commands/status.py` if endpoint inspection is surfaced there
- `weft/core/tasks/base.py` if a shared registration helper is added
- `weft/core/manager.py` only if endpoint discovery needs shared runtime-state
  helpers; manager spawn semantics must remain unchanged
- targeted tests under `tests/cli/` and `tests/tasks/`

Read first:

- `weft/core/tasks/base.py`
- `weft/core/tasks/multiqueue_watcher.py`
- `weft/core/manager.py`
- `weft/commands/queue.py`
- `tests/helpers/weft_harness.py`

Shared paths to reuse:

- runtime-state queue conventions in `weft/_constants.py`
- manager registry soft-state pattern in `weft/core/manager.py`
- queue-native CLI wiring in `weft/commands/queue.py`
- task lifecycle truth from `weft.log.tasks` and task-local queues

Current structure:

- Weft already exposes runtime-only registries through `weft.state.*` queues.
- Managers are task-shaped runtimes and already publish soft-state ownership in
  `weft.state.managers`.
- Queue commands stay thin over SimpleBroker and should remain the primary home
  for endpoint resolve and send ergonomics.
- Higher-level systems may host persistent tasks on Weft, but Weft itself does
  not currently own public service names or durable conversation semantics.

Comprehension checks before editing:

1. Can the implementer explain why a named endpoint must resolve to ordinary
   task-local queues rather than a new transport?
2. Can the implementer explain why duplicate-name handling belongs in runtime
   discovery, but replacement and handoff policy do not?

## 4. Invariants and Constraints

This slice must preserve all existing task, manager, and queue invariants.
Additional constraints for this feature:

- no new hidden database truth lane; runtime endpoint state must stay queue
  visible and runtime-only
- no direct SQL coupling to backend-specific broker schemas
- no change to TID generation or TID immutability
- no change to manager ownership of `weft.spawn.requests`
- no new implicit routing language inside the manager
- no automatic task spawn, autostart, or endpoint activation when a name is
  missing
- no mandatory request envelope, correlation format, or global service schema
- no new durable domain-state contract inside Weft core
- no drive-by refactor of queue or task abstractions

Review gates:

- endpoint resolution must stay a thin discovery primitive
- duplicate-name behavior must be deterministic and documented
- stale-record handling must be explicit and testable
- builtin demonstrations must remain optional and ordinary-task-shaped
- the implementation slice is not done until landed behavior is promoted from
  the relevant A-specs into the canonical specs that own current contract
- touched code and tests must keep bidirectional traceability to the governing
  canonical spec sections, not only to the A-specs
- the plan should receive an independent review pass before implementation, and
  the landed slice should receive an independent review before completion

## 5. Tasks

1. Define the runtime endpoint registry shape.
   Split this across the right layers. `01A` should define the component
   boundary. `05A` should define registration, refresh, release, stale-owner
   pruning, and canonical resolution. `04A` should only define how the runtime
   state rests on broker queues. Reuse the same soft-state style as
   `weft.state.managers`. Do not introduce a second task identity or a generic
   key-value API.

2. Define canonical resolution rules.
   Resolution should accept a project-local name and return one canonical live
   owner. The default conflict rule should stay minimal and deterministic. The
   current planned spec uses lowest eligible TID wins. If implementation
   pressure later suggests a different rule, update the planned spec first
   rather than silently changing the behavior.

3. Add thin CLI ergonomics over the registry.
   Keep this in queue-oriented command surfaces. A resolver may show the
   endpoint record. A send helper may write the provided payload to the
   resolved inbox. Control helpers may target `ctrl_in`. These commands must
   not imply a richer workflow contract than ordinary queue writes.

4. Add optional task-side helpers only where they reduce repeated low-level
   registry code.
   If shared registration code is useful, keep it narrow. It should help a
   persistent task claim, refresh, and release a name. It must not become a
   built-in actor framework or own service-specific schemas.

5. Keep richer semantics in builtins or higher layers.
   If Weft ships a service-style builtin later, that builtin may define its own
   inbox schema, request envelope, or explicit replacement flow. Those rules
   stay local to that builtin and must not silently become global runtime
   policy.

6. Promote landed behavior into canonical specs.
   Once a concrete implementation slice lands, move the then-current contract
   out of the relevant A-spec sections and into the canonical specs that own
   present-tense behavior. At minimum this likely means updating `01`,
   `05`, and `10`, and only keeping material in `04A`, `05A`, or other A-specs
   if it is still genuinely future-facing. Do not leave landed behavior
   stranded in planned companions.

7. Update spec-to-code and code-to-spec traces in the same slice.
   When implementation files gain ownership of endpoint registration,
   resolution, or CLI surfaces, update the canonical spec implementation
   mappings and the touched module or function docstrings together. New queue
   names, helpers, or CLI entry points should be traceable from the canonical
   spec to owning code and back again.

## 6. Verification

- Add task-level tests that prove active registration, release, stale-owner
  pruning, and duplicate-name resolution against the real broker path.
- Add CLI tests for endpoint resolve and endpoint-targeted queue writes.
- Verify runtime-only registry queues stay excluded from `system dump` and
  related persistence flows.
- Verify manager spawn behavior is unchanged. Named endpoints must not become a
  second submission path.
- Verify the landed canonical specs now describe the current behavior and the
  A-specs only retain still-planned material.
- Verify the touched implementation files and tests cite the governing
  canonical spec sections in module or function docstrings where they own the
  boundary.

## 7. Risks

The main risk is overreach. It is easy to take a useful `name -> endpoint`
primitive and quietly grow a service bus, request/reply framework, or hidden
domain store around it. That would blur Weft's boundary and make higher-level
systems harder to reason about, not easier.

The second risk is under-specifying conflict and liveness rules. If duplicate
claims or stale records are left fuzzy, operators will not know which task a
name resolves to, and higher-level systems will build inconsistent assumptions
on top.

The third risk is cutover pressure. Lowest-TID-wins is simple and Weft-like,
but it makes deliberate service replacement less convenient. If real users need
friendlier handoff semantics, add that above the core primitive rather than
teaching the core registry richer policy by accident.

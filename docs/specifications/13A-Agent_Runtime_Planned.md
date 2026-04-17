# Agent Runtime - Planned Follow-ups

This companion document tracks future agent-runtime work only. The canonical
current runtime contract lives in [13-Agent_Runtime.md](13-Agent_Runtime.md).
If a behavior is already shipped, it belongs in the canonical spec, not here.
If any item here ships, move it into the canonical sibling and remove it from
this planned companion rather than citing this file as the live runtime
contract.

Exploratory patterns for using Weft inside larger agent systems live in
[13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md).

Shipped surfaces intentionally out of scope here include the current `llm`
runtime, current `provider_cli` lanes, persistent `per_task` sessions already
covered in the canonical spec, and the current tool-profile, container-descriptor,
and cache-warming behavior.

## Planned Areas [AR-A1]

### Future Delegated Runtime Families [AR-A1.1]

Possible future work includes remote or service-backed runtimes and any new
delegated runtime family that still runs on the durable task spine defined in
the canonical agent-runtime spec.

Boundary:

- Weft still owns queues, lifecycle, control, resource limits, runner
  selection, and artifact persistence
- delegated runtimes own only the inner reasoning loop and runtime-specific
  execution details
- no second scheduler, daemon, or chat lane should appear

### Future Session Durability [AR-A1.2]

Future work may add restart-durable turn logs, replayable transcripts, or other
session state that survives process restarts.

Boundary:

- current task-scoped sessions remain canonical in
  [13-Agent_Runtime.md](13-Agent_Runtime.md)
- this companion only tracks anything beyond that
- persistent delegated work must stay task-scoped, not a separate public
  control plane

### Future Environment and Tool Profiles [AR-A2]

Future work may broaden environment-profile support, build-backed container
execution, runner-availability-aware validation, explicit mount policy, and
explicit shell/file/MCP bridge surfaces.

Boundary:

- environment choice remains explicit task or project policy
- runtime-specific convenience must not turn into hidden setup on ordinary
  `weft run`
- prompt-authored ad hoc Dockerfiles stay out of scope
- bounded lanes should stay capability-minimized by default

### Prepared Runtime Paths [AR-A3]

Future work may add explicit preparation helpers and cacheable prepared runner
artifacts for slow paths.

Boundary:

- preparation stays explicit and separate from ordinary execution
- hidden preparation on `weft run` is out of scope
- provider-ecosystem management is not the goal unless it is directly required
  to execute a task

## Non-Goals [AR-A4]

- no replacement of external agent frameworks with a Weft-native planner stack
- no second durable conversation store
- no hidden preflight in `weft run`
- no public protocol leak of private runtime-session messages

## Related Plans [AR-A5]

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md`](../plans/2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)

## Backlinks [AR-A6]

- Canonical current agent runtime behavior lives in
  [13-Agent_Runtime.md](13-Agent_Runtime.md).
- Exploratory patterns for higher-level systems live in
  [13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md).

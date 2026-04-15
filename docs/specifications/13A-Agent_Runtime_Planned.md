# Agent Runtime Planned

This companion document tracks intended but unimplemented agent-runtime work
adjacent to [13-Agent_Runtime.md](13-Agent_Runtime.md).

Its scope is narrow: planned Weft substrate work that expands how agent tasks
run while preserving the current task-first model.

Nothing here overrides the current canonical runtime contract.

Exploratory patterns for using Weft inside larger agent systems live in
[13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md).

## 1. Purpose [AR-A0]

The current runtime is enough for:

- one-off `llm` agent tasks
- persistent `per_task` sessions for the current `llm` backend
- current delegated `provider_cli` one-shot execution
- protocol-light public queues

Planned Weft-side growth should stay focused on substrate concerns:

- richer delegated runtimes beyond the current one-shot path
- stronger persistent task-lifetime delegated sessions
- explicit tool and environment profiles at the execution boundary
- runner-scoped environments, including declarative container builds
- explicit convenience surfaces over the same task and runner model

The boundary is deliberate:

- Weft may grow the runtime substrate for agent tasks
- Weft should not turn that runtime growth into a general agent-management or
  operator-workflow product

## 2. Planned Runtime Shape [AR-A1]

### 2.1 Additive runtime expansion

The current `llm` runtime should remain valid for bounded interpretation and
supervisor work. Planned higher-order support should be additive, not a
replacement.

Planned future runtime families include:

- delegated provider-backed agent runtimes that wrap external agent CLIs
- richer session-capable runtimes for persistent delegated work
- possible future remote or service-backed runtimes that still fit the same
  Weft task/session boundary

Constraint:

- Weft still owns queues, lifecycle, control, resource limits, runner
  selection, and artifact persistence
- delegated runtimes own only the inner reasoning loop and runtime-specific
  execution details

### 2.2 Same outer task model

Planned richer runtimes should still use the same outer task shape as the
current runtime:

- one-shot work remains an ordinary non-persistent task
- interactive or open-ended work remains an ordinary persistent task
- `STOP`, `KILL`, timeout, result boundaries, and reserved-queue policy stay on
  the Weft side

Future richer runtimes must extend the current
`TaskSpec -> Manager -> Consumer -> TaskRunner -> runtime/session` spine rather
than introducing a separate scheduler, daemon, or chat lane.

## 3. Persistent Delegated Sessions [AR-A2]

### 3.1 Task lifetime remains the owner

One planned substrate addition is stronger persistent delegated runtime support
for ordinary persistent agent tasks.

Intended shape:

- a delegated runtime may keep live session state for the lifetime of the task
- session teardown remains tied to normal task completion, failure, timeout,
  stop, or kill behavior
- task control stays Weft-native even when the delegated runtime has its own
  inner session concept

Constraint:

- persistent delegated sessions remain task-scoped, not a second public control
  plane
- provider-native session identifiers are cache metadata, not durable public
  truth

### 3.2 Restart-durable continuation stays out of scope here

The current planned substrate does not promise a full Weft-owned turn log or
restart-durable interactive transcript model.

Higher-level systems may build their own durable conversation or case model on
top of Weft. That usage pattern is described in
[13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md).

## 4. Execution Profiles [AR-A3]

### 4.1 Explicit tool profiles

Richer delegated runtimes need more than Python callables, but they should not
receive ambient machine powers by default.

Planned runtimes should support explicit tool profiles that decide which tool
surfaces are visible in a given task or session.

Planned tool classes include:

- Weft-resolved Python callables
- shell and file tools in approved environments
- allowlisted MCP server bridges
- future explicit delegated tool surfaces that still fit Weft task and runner
  boundaries

Constraint:

- tool exposure is profile-driven, not ambient from the user profile
- unknown or unverified tools should not appear automatically
- bounded lanes should be capability-minimized by default

### 4.2 Explicit environment profiles

Richer delegated runtimes also need explicit environment selection that stays
runner-aware rather than provider-specific.

Planned environment-profile support should let a task select:

- host execution with explicit working-directory policy
- declared image-backed container execution
- declarative build-backed container execution
- future sandbox forms that still fit the runner interface

Constraint:

- environment choice remains explicit task or project policy
- runtime-specific convenience must not turn into hidden setup on ordinary run

## 5. Runner-Scoped Environments [AR-A4]

### 5.1 Environment policy belongs at the runner boundary

Agent tasks often need stronger environment control than the current host-only
shape.

Planned richer runtimes should rely on runner-scoped execution environments
rather than burying environment policy inside the agent runtime itself.

Planned environment forms include:

- host execution with explicit working-directory and tool-profile policy
- container execution from a declared image
- container execution from a declarative checked-in build profile
- future sandbox forms that still fit the runner interface

Constraint:

- environment policy remains a runner concern
- the agent runtime selects tools and session behavior, not process isolation
- current internal provider container runtime descriptors for shipped
  Docker-backed delegated lanes are current behavior documented in
  [13-Agent_Runtime.md](13-Agent_Runtime.md); this planned section is about
  broader future runner-environment growth, not about re-planning that current
  descriptor boundary

### 5.2 Declarative build-backed containers

One planned execution surface is build-backed container execution for agent
tasks.

Intended shape:

- the task selects a checked-in environment profile
- that profile defines build context, base image or Dockerfile source,
  mount rules, environment injection rules, and network policy
- Weft or the runner may build the container image on demand and cache it for
  reuse

Constraint:

- prompt-authored ad hoc Dockerfiles are out of scope
- writable mounts must be explicit
- read-only mounts should be the default
- secrets and host capabilities must remain explicit runner policy, not prompt
  text

## 6. Planned Convenience Surfaces [AR-A5]

The public task and queue contract should stay small, but richer runtime
support will likely need explicit operator conveniences.

Possible future surfaces include:

- provider discovery and runtime-availability inspection
- environment-profile discovery
- explicit image- or environment-preparation helpers for slow runner paths
- interactive attach or follow helpers for persistent task-scoped sessions

Constraint:

- these are convenience surfaces over the same task and runner model
- they must remain explicit and optional
- they must not become hidden preflight or a second command language

## 7. Phased Enablement [AR-A6]

The substrate should not land in one plan. Each phase below should get its own
implementation plan in `docs/plans/`.

### Phase 1: Delegated One-Shot Runtime

Status:

- shipped as current behavior; see [13-Agent_Runtime.md](13-Agent_Runtime.md)
  for the canonical contract

Goal:

- add one richer delegated runtime for one-shot agent work without changing the
  current public queue model

Includes:

- provider registry and executable verification
- explicit provider selection with concrete non-interactive dispatch strings
- Weft-owned result, lifecycle, and artifact persistence

Excludes:

- persistent delegated sessions
- build-backed environments
- broad MCP bridging
- higher-layer thread or workflow semantics

### Phase 2: Persistent Delegated Sessions

Goal:

- let a persistent Weft agent task host an interactive delegated runtime for
  the life of that task

Includes:

- session-capable delegated runtime path
- persistent-task lifetime ownership for open-ended interaction
- task-local control, timeout, and cancellation over the live delegated session

Excludes:

- restart-durable continuation from Weft-owned turns
- build-backed environments
- higher-layer operator workflow

### Phase 3: Environment and Tool Profiles

Goal:

- make delegated lanes useful without ambient machine powers, while keeping
  environment policy runtime-agnostic rather than provider-specific

Includes:

- explicit environment profiles at the runner boundary
- runtime-availability-aware validation for runners, environments, providers,
  and tool surfaces
- profiles that stay usable across `host`, `docker`, `macos-sandbox`, and
  future runners
- declared image-backed and build-backed container profiles
- explicit mount rules
- explicit MCP-bridge and shell/file tool profiles

Excludes:

- broad autonomous action flows
- higher-layer session and policy truth

### Phase 4: Prepared Runtime Paths

Goal:

- reduce cold-start cost for slow but important runner paths without changing
  correctness rules

Includes:

- explicit runtime-preparation helpers for build-backed environments
- cacheable prepared images or other prepared runner artifacts
- clear separation between preparation and ordinary execution

Excludes:

- hidden preparation on `weft run`
- provider-ecosystem management that is not needed to execute a task

## Related Plans

- [`docs/plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md`](../plans/2026-04-13-spec-corpus-current-vs-planned-split-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md`](../plans/2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md`](../plans/2026-04-13-delegated-agent-runtime-phase-2-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md`](../plans/2026-04-13-delegated-agent-runtime-phase-3-implementation-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md`](../plans/2026-04-13-delegated-agent-authority-boundary-cleanup-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](../plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](../plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md)
- [`docs/plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md`](../plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md)
- [`docs/plans/2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md`](../plans/2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](../plans/2026-04-06-runner-extension-point-plan.md)
- [`docs/plans/2026-04-06-persistent-agent-runtime-implementation-plan.md`](../plans/2026-04-06-persistent-agent-runtime-implementation-plan.md)

## Backlinks

- Canonical current agent runtime behavior lives in
  [13-Agent_Runtime.md](13-Agent_Runtime.md).
- Exploratory patterns for higher-level systems live in
  [13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md).

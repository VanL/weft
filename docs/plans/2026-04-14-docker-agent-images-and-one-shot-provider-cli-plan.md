# Docker Agent Images and One-Shot Provider CLI Plan

This plan adds Docker-backed one-shot `provider_cli` agent execution with
reusable minimal provider images and an explicit builtin cache warmer. The
intent is narrow and practical: build the image once, create a fresh container
for each run, vary mounts/env/network per run, and keep Weft's durable task
spine unchanged.

This is risky boundary work. It crosses:

`TaskSpec -> validate -> TaskRunner -> runner plugin -> provider_cli prep -> Docker container -> queues/state log -> builtin TaskSpecs/docs`

The likely wrong moves are:

- moving Docker from the first-party extension package into `weft/core` during
  the same slice,
- creating a second execution path for agent tasks outside `TaskRunner`,
- copying host executables or ambient machine state into images,
- reusing long-lived containers when the requirement is per-run mounts and
  per-run network policy,
- keeping host-CLI preflight checks for Docker-backed agent tasks,
- making the builtin own image-building logic instead of calling shared code,
- and mocking away Docker, broker, or task lifecycle paths that can be tested
  for real.

Do not do those things.

## Goal

Add first-class Docker SDK image build/cache code for minimal provider runtime
images, use that code from a new builtin `prepare-agent-images`, and extend the
Docker runner to execute one-shot `spec.type="agent"` tasks for
`spec.agent.runtime="provider_cli"` by creating a fresh container per run with
explicit mounts, env, working directory, and network policy.

## Scope Lock

Implement exactly this slice:

1. keep Docker as a first-party optional extension under
   `extensions/weft_docker`
2. add shared agent-image recipe/build/cache code in that extension using the
   Docker SDK, including in-memory build contexts rather than on-disk
   `Dockerfile` materialization
3. support Docker-backed one-shot `provider_cli` agent tasks only for
   `spec.persistent=false` and
   `spec.agent.conversation_scope="per_message"`
4. support per-run file/directory mounts, explicit env injection, working
   directory selection, and network enabled/disabled through the existing
   runner/task surfaces
5. add a builtin `prepare-agent-images` that warms the image cache for
   supported provider recipes by calling the shared Docker image code
6. preserve existing host-runner `provider_cli` behavior and existing Docker
   command-runner behavior unless a small shared extraction is required

Do not implement any of the following in this slice:

- moving Docker implementation from `extensions/weft_docker` into `weft/core`
- persistent Docker-backed agent sessions
- `container.exec_run(...)` reuse as the primary per-run execution model
- a general image-prebuild plugin API for all runners
- a public agent-image override contract such as agent-side
  `runner.options.image` or `runner.options.build`
- build-time network controls or build-time secret handling beyond what the
  current Docker daemon already does
- copying host CLIs into images as a fallback
- automatic startup hooks or required prebuild steps
- broad migration of the existing Docker command runner from CLI shell-out to
  SDK containers unless a red test proves a tiny shared extraction is required

If implementation pressure pulls toward any excluded item, stop and split a
follow-on plan.

## Design Position

These decisions are fixed for this slice:

1. Docker stays in the first-party extension package.
   The code should be first-class and well-tested, but still optional. The
   feature is platform-limited and still settling. Making it core now would
   harden the wrong boundary too early.
2. Reuse the image, not the container.
   Mounts and network are container-level settings, not `exec`-level settings.
   The clean model is cached image plus fresh container per run.
3. The builtin is a cache warmer, not the owner of the feature.
   Real runs must call the same shared `ensure_agent_image(...)` path on cache
   miss. The builtin must not become a required setup step.
4. Provider image recipes are explicit.
   A host-discovered CLI does not imply a safe container recipe. If a provider
   lacks a deterministic non-interactive install path, mark it unsupported.
   Start with the smallest provider subset that meets that bar. Do not promise
   "all found providers" in code or docs unless each one has an explicit recipe
   and test coverage.
5. Runtime env capture must be explicit.
   Use `spec.env`, runner environment profiles, and explicit allowlists. Do not
   dump ambient `os.environ` into the container.
6. Host and Docker validation are different.
   Host-backed `provider_cli` runs may validate host executable resolution.
   Docker-backed one-shot runs must validate recipe availability and Docker
   readiness instead.
7. The public durable spine does not change.
   `TaskRunner` remains the execution facade. Outbox, control, result, state,
   and runner handle behavior stay on the current path.

## Source Documents

Primary specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.3], [CC-2.4], [CC-3]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.1], [TS-1.3]
- [`docs/specifications/06-Resource_Management.md`](../specifications/06-Resource_Management.md)
  [RM-5], [RM-5.1]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [IMMUT.1], [IMMUT.2], [STATE.1], [STATE.2], [QUEUE.1], [QUEUE.2],
  [QUEUE.3], [OBS.3], [IMPL.5], [IMPL.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.1], [CLI-1.4.1]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-0.1], [AR-2.1], [AR-5], [AR-7]
- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  [AR-A3], [AR-A5], [AR-A8]

Existing plans and local guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/plans/2026-04-06-runner-extension-point-plan.md`](./2026-04-06-runner-extension-point-plan.md)
- [`docs/plans/2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md`](./2026-04-13-delegated-agent-runtime-phase-1-implementation-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md`](./2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md)

Source spec gap:

- There is no current spec section that fully defines Docker-backed
  `provider_cli` image preparation, one-shot container execution, or the
  `prepare-agent-images` builtin. This implementation must add the missing spec
  coverage as part of the work. Do not treat code as the only source of truth.

## Context and Key Files

Files that will likely change in this slice:

- docs/specs and docs:
  - `docs/specifications/02-TaskSpec.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `docs/specifications/10B-Builtin_TaskSpecs.md`
  - `docs/specifications/13-Agent_Runtime.md`
  - `docs/specifications/13A-Agent_Runtime_Planned.md`
  - `README.md` only if the user-facing runtime/builtin explanation needs one
    short update
- core agent/runtime code:
  - `weft/core/agents/validation.py`
  - `weft/core/tasks/runner.py`
  - `weft/core/agents/backends/provider_cli.py`
  - `weft/core/agents/provider_cli/registry.py`
  - recommended new shared helper:
    `weft/core/agents/provider_cli/execution.py`
  - likely small probe-helper extraction in
    `weft/core/agents/provider_cli/probes.py` or a
    neighboring helper if needed for reuse by both builtins
- builtins:
  - `weft/builtins/agent_probe.py`
  - recommended new builtin function module:
    `weft/builtins/agent_images.py`
  - new builtin asset:
    `weft/builtins/tasks/prepare-agent-images.json`
- Docker extension:
  - `extensions/weft_docker/weft_docker/plugin.py`
  - recommended new modules:
    - `extensions/weft_docker/weft_docker/images.py`
    - `extensions/weft_docker/weft_docker/agent_images.py`
    - `extensions/weft_docker/weft_docker/agent_runner.py`
  - `extensions/weft_docker/pyproject.toml` only if package exports or tests
    need tightening
- tests:
  - `tests/core/test_agent_validation.py`
  - `tests/core/test_provider_cli_backend.py`
  - recommended new focused test:
    `tests/core/test_provider_cli_execution.py`
  - `tests/tasks/test_agent_execution.py`
  - recommended new Docker-backed agent test:
    `tests/tasks/test_docker_agent_execution.py`
  - `tests/cli/test_cli_run.py`
  - `tests/cli/test_cli_spec.py`
  - `extensions/weft_docker/tests/test_docker_plugin.py`
  - recommended new extension tests:
    `extensions/weft_docker/tests/test_agent_images.py`

Read first:

1. `weft/core/tasks/runner.py`
   Current execution facade. This is the only supported public entry point for
   runner-backed execution. Do not bypass it.
2. `weft/core/agents/validation.py`
   Current static/preflight validation seam. This is where host-only preflight
   assumptions currently leak into Docker-backed agent work.
3. `weft/core/agents/backends/provider_cli.py`
   Current one-shot and session backend. This already knows how to prepare
   prompts, temp files, and parse provider-specific outputs, but it currently
   assumes host execution.
4. `weft/core/agents/provider_cli/registry.py`
   Provider-specific invocation builders. This file contains the hidden
   temp-file and executable-path couplings that matter most for Docker.
5. `extensions/weft_docker/weft_docker/plugin.py`
   Current Docker command runner plugin. It is command-only today and uses a
   hybrid CLI plus SDK approach.
6. `weft/builtins/agent_probe.py`
   Current builtin probe helper. It writes missing defaults into
   `.weft/agents.json`. The new image-prep builtin must not depend on that side
   effect.
7. `tests/tasks/test_agent_execution.py`
   Current end-to-end proof for agent execution. Extend this contract; do not
   invent a different public output shape.
8. `tests/tasks/test_command_runner_parity.py`
   Current pattern for real runner parity tests and real-Docker skip behavior.
9. `extensions/weft_docker/tests/test_docker_plugin.py`
   Current focused Docker extension tests and fake-client style.

Shared paths and helpers to reuse:

- `TaskRunner` in `weft/core/tasks/runner.py`
- existing `provider_cli` provider registry and output parsers in
  `weft/core/agents/provider_cli/registry.py`
- `resolve_provider_cli_executable(...)` and related provider helpers for the
  host path
- `require_runner_plugin(...)` in `weft/_runner_plugins.py`
- builtin task asset discovery in `weft/builtins/__init__.py`
- current Docker runner metadata/handle helpers in
  `extensions/weft_docker/weft_docker/plugin.py`
- `run_cli()` in `tests/conftest.py`
- `WeftTestHarness` in `tests/helpers/weft_harness.py`

Current structure that matters:

- `TaskRunner.__init__()` currently triggers agent runtime validation, and the
  current `provider_cli` validation path still assumes host executable
  resolution.
- `ProviderCLIBackend.validate(..., preflight=True)` currently resolves a host
  executable and can still fail before the real run starts. That is correct for
  host runs and wrong for Docker-backed one-shot runs.
- provider-specific invocation builders create temp files and output paths on
  the host filesystem. Docker-backed runs must mount those paths into the
  container or provide container-visible equivalents.
- the Docker runner currently accepts command-task `runner.options.image` or
  `runner.options.build`; agent image selection should not reuse that public
  contract in this slice.
- `probe-agents` is a builtin TaskSpec with a normal Python function target.
  `prepare-agent-images` should follow the same packaging pattern.

## Read This First

Read these in order before touching code:

1. [`AGENTS.md`](../../AGENTS.md)
2. [`docs/agent-context/README.md`](../agent-context/README.md)
3. [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
4. [`docs/agent-context/principles.md`](../agent-context/principles.md)
5. [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
6. [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
7. [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
8. [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
9. [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](./2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
10. [`weft/core/tasks/runner.py`](../../weft/core/tasks/runner.py)
11. [`weft/core/agents/validation.py`](../../weft/core/agents/validation.py)
12. [`weft/core/agents/backends/provider_cli.py`](../../weft/core/agents/backends/provider_cli.py)
13. [`weft/core/agents/provider_cli/registry.py`](../../weft/core/agents/provider_cli/registry.py)
14. [`extensions/weft_docker/weft_docker/plugin.py`](../../extensions/weft_docker/weft_docker/plugin.py)
15. [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
16. [`tests/tasks/test_command_runner_parity.py`](../../tests/tasks/test_command_runner_parity.py)

## Comprehension Questions

If the implementer cannot answer these before editing, they are not ready:

1. Which current validation call path still assumes host executable resolution
   for `provider_cli`, and why does that break Docker-backed one-shot runs?
2. Why does a reused container plus `exec_run()` fail the stated requirement
   around per-run mounts and per-run network policy?
3. Which current provider invocation builders emit host temp-file paths that
   must stay visible to the process that parses provider output?
4. Why must `prepare-agent-images` call shared build/cache code directly rather
   than parsing the human output of `probe-agents`?

## Invariants and Constraints

The following must stay true:

- the current durable spine remains the only execution path
- TIDs, queue names, reserved-queue policy, and forward-only state transitions
  remain unchanged
- `spec` and `io` stay immutable after resolved `TaskSpec` creation
- Docker remains optional and platform-limited in this slice
- host-backed `provider_cli` behavior keeps working
- current Docker command-runner behavior keeps working
- Docker-backed agent runs are one-shot only in this slice
- persistent `provider_cli` sessions remain on the current host-backed path
- real agent runs can build on cache miss; the builtin is optional
- runtime env, working dir, mounts, and network are per-run inputs and must not
  enter the image cache key
- image recipes are explicit and deterministic; unsupported providers fail
  clearly
- build-time Dockerfile/context generation must not require writing a physical
  `Dockerfile` to disk
- no ambient environment slurp
- no new dependency beyond the existing Docker extra and whatever it already
  carries

Hidden couplings to call out explicitly:

- `provider_cli` preflight today is coupled to host executable resolution
- provider-specific temp files and output files are coupled to path visibility
- builtin asset existence and builtin docs are coupled by the contract in
  `10B-Builtin_TaskSpecs.md`
- current Docker command-runner validation owns network/mount/limit semantics
  that Docker-backed agent runs should reuse where possible
- `weft system builtins`, `weft spec list`, and `weft run --spec` are all part
  of the public builtin contract

Review gates for this slice:

- no Docker move into core
- no second runner or agent execution path
- no new broad plugin API for image preparation
- no public agent image override contract
- no mock-heavy substitute for real queue/task/Docker proof where the real path
  is practical
- no doc drift between specs, builtin catalog, and shipped behavior

Out of scope:

- persistent Docker agent sessions
- image GC or registry publishing
- build-time secret management
- cross-run container reuse
- provider auto-install on the host
- Docker Desktop or daemon provisioning
- non-Docker container runtimes

## Rollout and Rollback

Rollout:

- ship the shared image service and Docker one-shot agent path behind the
  existing explicit Docker runner contract
- keep host `provider_cli` as the default path unless a TaskSpec explicitly
  chooses the Docker runner
- ship `prepare-agent-images` as an optional builtin helper only after the
  shared image path exists

Rollback:

- revert the Docker-backed agent validation/execution path and the builtin
  together
- leave host `provider_cli` and Docker command-runner code intact
- if docs were updated to current-contract language in the same change, revert
  those docs in the same rollback

One-way doors:

- the builtin name `prepare-agent-images` is public once shipped
- any current-spec wording that claims Docker-backed agent support is live must
  only land with code and tests in the same change

## Tasks

1. Extract transport-neutral one-shot `provider_cli` preparation and split the
   validation boundary.
   - Outcome: host and Docker one-shot paths share one prompt/tool-profile/
     provider-invocation preparation path, and validation no longer requires a
     host CLI when the run is explicitly Docker-backed.
   - Files to touch:
     - `weft/core/agents/validation.py`
     - `weft/core/tasks/runner.py`
     - `weft/core/agents/backends/provider_cli.py`
     - `weft/core/agents/provider_cli/registry.py`
     - new `weft/core/agents/provider_cli/execution.py`
     - `tests/core/test_agent_validation.py`
     - `tests/core/test_provider_cli_backend.py`
     - new `tests/core/test_provider_cli_execution.py`
     - `tests/tasks/test_agent_execution.py`
   - Read first:
     - `weft/core/agents/validation.py`
     - `weft/core/agents/backends/provider_cli.py`
     - `weft/core/agents/provider_cli/registry.py`
     - `tests/tasks/test_agent_execution.py`
   - Required action:
     - extract a narrow shared preparation object for one-shot `provider_cli`
       execution. It should carry prepared argv, provider metadata, temp-file
       requirements, env additions, working directory, and the parser path for
       the provider result.
     - keep host execution on the current host path, but make it consume the
       shared preparation object rather than rebuilding its own command shape
     - teach validation to branch on execution context:
       host-backed one-shot runs still validate host executable resolution;
       Docker-backed one-shot runs validate Docker recipe support instead
     - do not change persistent `provider_cli` session validation in this task
     - keep public output aggregation unchanged
   - Red-green TDD:
     - first add a failing validation test that proves a Docker-backed one-shot
       `provider_cli` spec no longer requires a host executable during
       preflight
     - then add a failing host-path regression test proving host-backed runs
       still validate host executable resolution
     - then add focused shared-preparation tests around prompt assembly,
       provider options, and temp-file/output path generation
   - Keep real:
     - real provider fixture wrappers in `tests/fixtures/provider_cli_fixture.py`
     - real `TaskRunner` execution for host regression tests
   - Do not mock:
     - the provider registry when proving prompt and option shaping
     - `TaskRunner` when proving host behavior
   - Stop if:
     - a second prompt-building path appears
     - the code starts adding Docker-specific logic deep inside provider parser
       code rather than at the execution boundary
   - Done when:
     - host tests still pass
     - Docker-specific preflight no longer depends on host CLI presence
     - the shared preparation object is small, typed, and reused by both paths

2. Add explicit Docker agent image recipes and shared SDK build/cache helpers.
   - Outcome: the Docker extension owns deterministic provider-image recipes
     and can build or reuse them without writing a physical `Dockerfile` to
     disk.
   - Files to touch:
     - new `extensions/weft_docker/weft_docker/images.py`
     - new `extensions/weft_docker/weft_docker/agent_images.py`
     - `extensions/weft_docker/weft_docker/plugin.py` only for minimal wiring
     - `extensions/weft_docker/tests/test_docker_plugin.py`
     - new `extensions/weft_docker/tests/test_agent_images.py`
   - Read first:
     - `extensions/weft_docker/weft_docker/plugin.py`
     - `extensions/weft_docker/tests/test_docker_plugin.py`
     - Docker SDK use in the existing plugin
   - Required action:
     - add a recipe dataclass or equivalent explicit contract for provider
       images. At minimum include provider name, recipe version, base image,
       inline Dockerfile content or deterministic build steps, any baked static
       support files, and the in-container executable/entrypoint contract.
     - start with the smallest supported provider set that has deterministic,
       non-interactive container recipes and fixture-backed tests. Report other
       discovered providers as `unsupported`; do not invent placeholder recipes.
     - compute a deterministic cache key from image-defining inputs only:
       provider name, recipe version, base image, baked files, and any static
       build args
     - exclude per-run mounts, env, working dir, and network from the cache key
     - use Docker SDK build APIs with in-memory tar context or file-like
       Dockerfile input
     - label images with enough metadata to debug cache reuse, but do not
       document the exact tag shape as a public API
     - support `refresh=True` by forcing a rebuild of the same recipe rather
       than pretending the existing image is good enough
   - Red-green TDD:
     - add a failing unit test that proves cache-key stability across changing
       runtime mounts/env/network
     - add a failing unit test that proves the build helper passes an in-memory
       build context to the Docker SDK rather than a path-based Dockerfile
     - add a failing unit test that unsupported providers are reported clearly
       and never fall back to host-binary copying
   - Keep real:
     - the image recipe objects and cache-key computation
   - Mock only:
     - the Docker SDK client boundary
   - Stop if:
     - the recipe layer starts deriving image contents from host executable
       paths or login state
     - `plugin.py` starts absorbing all image logic instead of delegating to the
       new module
   - Done when:
     - one helper can answer "build or reuse this provider image"
     - the build path never requires an on-disk `Dockerfile`
     - tests prove cache-key and unsupported-provider behavior

3. Extend the Docker runner with one-shot `provider_cli` agent execution.
   - Outcome: `spec.type="agent"` plus `runner.name="docker"` works for the
     narrow one-shot `provider_cli` lane by creating a fresh container per run
     and reusing the shared image service.
   - Files to touch:
     - `extensions/weft_docker/weft_docker/plugin.py`
     - new `extensions/weft_docker/weft_docker/agent_runner.py`
     - `weft/core/tasks/runner.py` only if metadata/handle plumbing needs a
       narrow addition
     - `tests/tasks/test_agent_execution.py`
     - new `tests/tasks/test_docker_agent_execution.py`
     - `extensions/weft_docker/tests/test_docker_plugin.py`
   - Read first:
     - `extensions/weft_docker/weft_docker/plugin.py`
     - `tests/tasks/test_command_runner_parity.py`
     - `tests/tasks/test_agent_execution.py`
   - Required action:
     - extend plugin validation to accept agent tasks only for:
       `spec.agent.runtime="provider_cli"`,
       `spec.persistent=false`, and
       `spec.agent.conversation_scope="per_message"`
     - reject Docker-backed persistent agent tasks with a clear error that
       points back to the host path
     - resolve the provider image through the shared recipe/cache helper rather
       than through `runner.options.image` or `runner.options.build`
     - create a fresh container per run with:
       - explicit env injection from `spec.env` and any materialized runner
         environment profile values
       - mounted workspace paths and mounted files from existing runner options
       - a mounted runtime tempdir for provider temp files and output files
       - working directory semantics consistent with existing Docker runner
       - network disabled when explicitly requested or when
         `limits.max_connections == 0`
     - use Docker SDK container APIs for the new agent path
     - persist a runtime handle with the container identity so `status`, `stop`,
       and `kill` continue to work through the existing plugin control path
     - feed container results back through the shared `provider_cli` output
       parser so public agent output stays identical
   - Red-green TDD:
     - add a failing validation test for Docker-backed persistent agent specs
     - add a failing end-to-end Docker agent test that proves env, cwd, and
       mounted file visibility inside the provider CLI
     - add a failing end-to-end Docker agent test that proves network mode can
       vary per run without changing the cache key
     - add a failing runtime-handle test or status test that proves the task is
       still described as runner `docker`
   - Keep real:
     - real broker-backed task execution through `TaskRunner` and `Consumer`
     - real Docker daemon in narrow parity tests when available
   - Mock only:
     - Docker SDK client boundary in focused unit tests
   - Do not mock:
     - queue/state behavior
     - task lifecycle
     - public output aggregation
   - Stop if:
     - the implementation starts creating a second agent path outside
       `TaskRunner`
     - the new path shells out to a host provider executable
     - the code tries to reuse containers across runs in this slice
   - Done when:
     - a Docker-backed one-shot provider fixture run succeeds end to end
     - the public output matches the host-runner equivalent
     - per-run env/mount/network variation works without rebuilding the image

4. Add the `prepare-agent-images` builtin as a thin cache warmer.
   - Outcome: Weft ships an explicit builtin TaskSpec that warms Docker agent
     images for supported providers without owning the image logic.
   - Files to touch:
     - new `weft/builtins/agent_images.py`
     - new `weft/builtins/tasks/prepare-agent-images.json`
     - `weft/builtins/agent_probe.py` only if a tiny shared probe helper
       extraction is required
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `tests/cli/test_cli_run.py`
     - `tests/cli/test_cli_spec.py`
   - Read first:
     - `weft/builtins/agent_probe.py`
     - `weft/builtins/tasks/probe-agents.json`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
   - Required action:
     - create the builtin as an ordinary builtin TaskSpec with a Python
       function target in core
     - inside the builtin, lazily resolve the Docker image helper from the
       Docker extension and surface the same install hint style as missing
       runner plugins if the extra is unavailable
     - use one explicit input contract only:
       `{"providers": ["codex"], "refresh": false}`
       where `providers` is optional and `refresh` defaults to `false`
     - when no providers are specified, use shared probe logic to choose the
       default set, but do not parse `probe-agents` text output and do not
       require prior execution of `probe-agents`
     - do not mutate `.weft/agents.json` by default
     - return structured output with at least:
       `project_root`, `summary`, and one report per provider with `supported`,
       `built`, `reused`, `unsupported`, or `failed`
   - Red-green TDD:
     - add a failing CLI test that the builtin resolves and runs through
       `weft run --spec prepare-agent-images`
     - add a failing test that local stored specs still shadow the builtin
     - add a failing test that the builtin does not create or modify
       `.weft/agents.json` by default
   - Keep real:
     - real builtin asset lookup through the shipped package tree
     - real CLI subprocesses through `run_cli()`
   - Mock only:
     - the extension image helper boundary in focused builtin unit tests if a
       direct CLI proof would otherwise require Docker
   - Stop if:
     - the builtin starts re-implementing image recipes or build logic
     - the builtin starts treating `probe-agents` settings writes as a required
       precondition
   - Done when:
     - the builtin is a thin wrapper around shared code
     - builtin resolution and output shape are locked by black-box tests

5. Update specs, builtin docs, and traceability only after the code path is
   real.
   - Outcome: current docs describe the shipped behavior honestly, planned docs
     describe only what still remains, and spec-plan traceability is
     bidirectional.
   - Files to touch:
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/13A-Agent_Runtime_Planned.md`
     - `README.md` only if needed
   - Read first:
     - the final code and tests from tasks 1 through 4
     - current docs named above
   - Required action:
     - document the Docker-backed one-shot `provider_cli` lane and keep its
       limits explicit:
       one-shot only, `provider_cli` only, fresh container per run, persistent
       sessions still host-only
     - document runtime env/mount/network as per-run inputs
     - document the `prepare-agent-images` builtin and note that it is optional
       and recipe-limited
     - document that host discovery and container recipe support are different
       facts
     - add this plan to the `## Related Plans` section of every touched spec
   - Stop if:
     - the docs start implying Docker support for all providers regardless of
       recipe support
     - the docs start implying Docker moved into core
   - Done when:
     - a zero-context reader can tell what is supported now, what stays on the
       host path, and what the builtin is for

6. Run the full verification pass and independent review loops before merge.
   - Outcome: the slice is proven through the real seams that matter and has a
     review record on the risky boundaries.
   - Files to touch:
     - none unless fixes are needed
   - Read first:
     - `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
   - Required action:
     - run focused tests after each task, not only at the end
     - run one independent review pass after task 3, because that is the point
       where the execution-boundary change is real
     - run one final independent review pass before merge
     - compare docs against shipped behavior before calling the slice done
   - Stop if:
     - the easiest passing test plan becomes "mock everything"
     - rollback cannot be described cleanly
   - Done when:
     - all required tests pass
     - docs/specs match behavior
     - review comments on the boundary decisions are resolved

## Test Matrix

Use the smallest proof that exercises the real contract.

Unit tests:

- `tests/core/test_provider_cli_execution.py`
  proves shared prompt/invocation preparation and path handling
- `tests/core/test_agent_validation.py`
  proves host vs Docker validation split
- `extensions/weft_docker/tests/test_agent_images.py`
  proves recipe support, cache key, and Docker SDK build-input behavior
- `extensions/weft_docker/tests/test_docker_plugin.py`
  proves validation and runtime-handle behavior at the extension boundary

End-to-end tests:

- `tests/tasks/test_agent_execution.py`
  host regression proof
- `tests/tasks/test_docker_agent_execution.py`
  real Docker-backed one-shot agent proof with fixture providers and mounted
  temp paths
- CLI builtin tests in `tests/cli/test_cli_run.py` and `tests/cli/test_cli_spec.py`

What not to mock:

- `simplebroker.Queue`
- `TaskRunner`
- `Consumer`
- builtin asset resolution
- provider registry when testing provider option/prompt shaping

What may be mocked:

- Docker SDK client objects in focused unit tests
- missing-extension import boundary for builtin error handling
- runtime daemon availability probes when testing skip behavior

Specific invariants to assert:

- Docker-backed one-shot agent preflight does not require a host CLI when a
  recipe exists
- host-backed `provider_cli` preflight still validates the host executable
- changing mounts, env, or network does not change the image cache key
- unsupported providers are reported as unsupported; they do not silently fall
  back to host copies
- cache miss during a real Docker agent run still succeeds by building on
  demand
- `prepare-agent-images` is optional and does not mutate `.weft/agents.json`
- Docker-backed persistent agent specs are rejected clearly
- Docker-backed one-shot provider output matches the host path for the same
  provider fixture

Suggested commands:

```bash
. ./.envrc
uv sync --all-extras
./.venv/bin/python -m pytest tests/core/test_agent_validation.py tests/core/test_provider_cli_backend.py tests/core/test_provider_cli_execution.py -q
./.venv/bin/python -m pytest extensions/weft_docker/tests/test_docker_plugin.py extensions/weft_docker/tests/test_agent_images.py -q
./.venv/bin/python -m pytest tests/tasks/test_agent_execution.py tests/tasks/test_docker_agent_execution.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_run.py tests/cli/test_cli_spec.py -k 'builtin or prepare-agent-images' -q
./.venv/bin/python -m mypy weft extensions/weft_docker/weft_docker
./.venv/bin/ruff check weft tests extensions/weft_docker
```

Manual smoke checks on a machine with Docker available:

1. Run `weft run --spec prepare-agent-images` in a repo with no prior Docker
   agent images and confirm at least one supported provider reports `built`
   or `unsupported`, not a hidden host fallback.
2. Run a one-shot `provider_cli` TaskSpec through the Docker runner with a
   mounted temp directory and confirm the provider fixture sees the mounted
   input and runtime env.
3. Repeat the run with a different mounted directory and different `network`
   setting and confirm the image is reused while the container behavior changes.

## Review Loops and Acceptance Gates

Review loops:

- After task 1: review the validation split and shared execution object before
  adding Docker-specific behavior.
- After task 3: independent review of the execution boundary, especially temp
  path mounting, runtime handle behavior, and scope discipline.
- Final: independent review of docs, builtin contract, and rollback story.

Acceptance gates:

- Docker is still implemented in `extensions/weft_docker`, not moved into core.
- The builtin is thin. Shared image logic is not duplicated.
- The real run path works without requiring the builtin first.
- The slice is still one-shot only for Docker-backed agent tasks.
- Tests prove behavior at the public seams rather than only through internal
  helper assertions.
- Current specs, planned specs, builtin catalog, and shipped behavior agree.

Future follow-up, not part of this slice:

- revisit whether Docker belongs in core only after the command and agent
  contracts settle, the optional-platform story is still worth the cost, and
  there is evidence that the extension boundary is causing more harm than help
  in real maintenance work

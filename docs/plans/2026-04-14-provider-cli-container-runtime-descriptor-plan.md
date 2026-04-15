# Provider CLI Container Runtime Descriptor Plan

## Goal

Add a small internal "config doc" system for Docker-backed delegated
`provider_cli` runtimes so Weft can resolve provider-specific runtime
requirements such as allowlisted env vars and optional read-only config mounts
like `.codex` without baking that knowledge into ad hoc Python branches. Keep
the public `TaskSpec` contract unchanged, keep provider invocation building in
code, and start with one concrete descriptor for `codex`.

Ownership for this slice is explicit:

- provider container runtime descriptors are core `provider_cli` metadata and
  belong under `weft/core/agents/provider_cli/`
- Docker image recipes and Docker SDK build/run mechanics remain in the
  first-party Docker package under `extensions/weft_docker/`

Do not blur those layers.

This is boundary work. It touches the current Docker-backed one-shot delegated
agent lane, explicit validation behavior, builtin examples, and the internal
split between provider adapter code and runner/container policy.

The likely wrong moves are:

- turning the descriptor into a shell mini-language
- adding new public `TaskSpec` fields for provider container wiring
- moving provider invocation building out of code and into JSON templating
- copying host config or secrets into images by default
- treating descriptor-required env/files as hidden preflight gates on ordinary
  `weft run`
- widening the current support matrix beyond the providers with both a real
  image recipe and a real runtime descriptor
- moving Docker image/build/run mechanics into core as part of this slice
- inventing a "required extension" packaging state that stays separate in code
  layout while pretending to be mandatory in product semantics

Do not do those things.

## Scope Lock

Implement exactly this slice:

1. add an internal typed provider container runtime descriptor model backed by
   packaged JSON assets
   - this model lives in core `provider_cli`, not in the Docker package
2. support descriptor-driven runtime requirements for Docker-backed
   `provider_cli` runs:
   - allowlisted env forwarding from the host
   - optional and required runtime mounts
   - read-only-by-default config mounts
   - deterministic target paths inside the container
3. keep the current provider adapter code as the owner of actual CLI argv
   construction and output parsing
4. keep the current Docker image recipe mechanism, but require a provider to
   have both:
   - an image recipe
   - a container runtime descriptor
   before Weft claims Docker-backed support for that provider
5. migrate `codex` to the new descriptor as the first and only required
   provider slice
6. update the shipped `example-dockerized-agent` builtin so provider-wide auth
   and config needs come from the descriptor, while the builtin keeps only its
   repo-specific doc mount
7. make explicit validation able to surface descriptor-required env or mount
   problems if the existing validation seam is the smallest clean place to do
   that

Do not implement any of the following in this slice:

- new public `TaskSpec` schema for provider container wiring
- descriptor-driven arbitrary command templates or output parsing
- login automation or scraping of ambient browser/session state
- copying host config dirs such as `.codex` into the image
- persistent Docker-backed agent sessions
- support for providers beyond `codex` unless a second provider is proven and
  fully specified during the same slice
- a general image-build DSL in JSON
- live log streaming work; that is a separate runner improvement
- moving the Docker runner package into core
- introducing a new "required extension" packaging mode

If implementation pressure pulls toward any excluded item, stop and split a
follow-on plan.

## Design Position

These decisions are fixed for this slice:

1. The config doc is descriptive, not procedural.
   The descriptor says what the runtime needs. It does not become a new
   command language.
2. Provider invocation stays in code.
   `weft/core/agents/provider_cli/registry.py` already owns provider-specific
   argv building and parse behavior. Reuse that path. Do not duplicate it in
   JSON.
3. The descriptor is internal metadata, not a new public API.
   It exists to support Docker-backed delegated runtimes and explicit
   validation/diagnostics. It does not add user-authored `TaskSpec` knobs.
4. Config mounts default to read-only.
   For things like `.codex`, prefer optional read-only runtime mounts, not
   image copies and not read-write by default.
5. Explicit user inputs win.
   `spec.env` and explicit `runner.options.mounts` must remain authoritative.
   Descriptor defaults fill gaps; they do not silently override user-authored
   runtime policy.
6. Ordinary run still attempts the real run.
   Missing descriptor-required env or files may be surfaced by explicit
   validation, but they must not turn normal `weft run` into a speculative
   readiness gate.
7. Keep the durable spine unchanged.
   `TaskSpec -> Manager -> Consumer -> TaskRunner -> runner plugin ->
   queues/state log` remains the only execution path.
8. Provider runtime metadata lives in core; Docker mechanics do not.
   The descriptor schema, packaged descriptor assets, and runtime-requirement
   resolution belong in `weft/core/agents/provider_cli/`. Docker-specific
   image recipes, Docker SDK calls, and container lifecycle handling stay in
   `extensions/weft_docker/`.
9. Do not solve packaging strategy here.
   For this slice, treat Docker as a first-party supported runner package with
   optional installation. Do not add a half-state where Docker is packaged as
   a separate "required extension".

## Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1.3]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-0.1], [AR-2.1], [AR-5], [AR-7]
- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  Phase 4: Prepared Runtime Paths

Existing plans and guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](./2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md)

Source-spec gap:

- There is no current spec section that defines an internal provider container
  runtime descriptor or the exact split between descriptor-owned runtime
  requirements and code-owned provider invocation logic. This work must add the
  missing current-state description in the touched specs instead of leaving the
  contract implicit in code.

## Context and Key Files

Files to modify:

- provider CLI core:
  - `weft/core/agents/provider_cli/registry.py`
  - recommended new module:
    `weft/core/agents/provider_cli/container_runtime.py`
  - recommended packaged data directory:
    `weft/core/agents/provider_cli/runtime_descriptors/`
  - `weft/core/agents/provider_cli/execution.py`
  - `weft/core/agents/validation.py`
- first-party Docker package:
  - `extensions/weft_docker/weft_docker/agent_runner.py`
  - `extensions/weft_docker/weft_docker/plugin.py`
  - `extensions/weft_docker/weft_docker/agent_images.py`
- builtin example:
  - `weft/builtins/tasks/example-dockerized-agent/example_dockerized_agent.py`
  - `weft/builtins/tasks/example-dockerized-agent/taskspec.json` only if the
    example wording or refs must tighten
- docs:
  - `docs/specifications/13-Agent_Runtime.md`
  - `docs/specifications/13A-Agent_Runtime_Planned.md`
  - `docs/specifications/10B-Builtin_TaskSpecs.md`
  - `docs/specifications/02-TaskSpec.md`
  - `README.md` only if one short sentence is needed for discoverability
- tests:
  - recommended new tests:
    - `tests/core/test_provider_cli_container_runtime.py`
    - `extensions/weft_docker/tests/test_container_runtime_resolution.py`
  - existing tests to extend:
    - `tests/core/test_agent_validation.py`
    - `tests/core/test_builtin_example_dockerized_agent.py`
    - `extensions/weft_docker/tests/test_docker_plugin.py`
    - `extensions/weft_docker/tests/test_agent_runner.py`

Read first:

1. `weft/shell/known_interpreters.py`
   Small precedent for internal capability metadata. Use its spirit, not its
   exact size.
2. `weft/core/agents/provider_cli/registry.py`
   Current owner of provider-specific invocation building and option/model
   validation.
3. `weft/core/agents/provider_cli/execution.py`
   Current shared one-shot prompt/tool-profile/invocation preparation path.
4. `extensions/weft_docker/weft_docker/agent_runner.py`
   Current Docker-backed one-shot runner. This is where runtime mounts/env are
   actually merged into container creation today.
5. `extensions/weft_docker/weft_docker/agent_images.py`
   Current image recipe registry. The new descriptor must not accidentally
   duplicate image-recipe ownership.
6. `weft/core/environment_profiles.py`
   Current merging behavior for runner defaults vs explicit task values.
7. `weft/builtins/example_dockerized_agent.py`
   Current ad hoc repo-specific environment profile. This is the clearest proof
   that provider-wide runtime needs are currently in the wrong place.

Comprehension questions before editing:

1. Which file currently owns the exact `codex exec ...` argv shape?
   Answer: `weft/core/agents/provider_cli/registry.py`.
2. Which layer currently merges runner profile defaults with explicit task env
   and mounts?
   Answer: `weft/core/environment_profiles.py` plus the Docker runner classes.
3. Which path must remain the only execution path for Docker-backed one-shot
   provider runs?
   Answer: `TaskSpec -> Manager -> Consumer -> TaskRunner -> runner plugin ->
   queues/state log`.

## Invariants and Constraints

Must not change:

- public `TaskSpec` schema
- public agent work-envelope contract
- current provider adapter ownership of argv building and output parsing
- no hidden validation or health probe on ordinary `weft run`
- no second runtime path outside `TaskRunner` and the Docker runner plugin
- no copying host config or secrets into the image by default
- no read-write default for config directories like `.codex`
- no movement of Docker SDK/image/build/run ownership into core during this
  slice
- no broad provider support claim beyond the providers with both:
  - a real image recipe
  - a real runtime descriptor

Hidden couplings to call out explicitly:

- `example-dockerized-agent` currently forwards `OPENAI_API_KEY` in a builtin
  environment profile, which is provider-wide behavior living in repo-specific
  example code
- `DockerProviderCLIRunner` currently merges only task env and explicit mounts;
  descriptor-driven defaults must fit that merge path rather than create a new
  one
- `CodexProvider.build_invocation()` currently assumes the CLI can use `-C` and
  `-o` with a temp output file; descriptor work must not break the current temp
  file contract
- `prepare-agent-images` currently owns only image warming; it must not become
  a runtime requirement resolver or second control plane

Error-path priorities:

- malformed packaged descriptor is fatal and should fail validation loudly
- missing optional config mounts such as `.codex` are best-effort and should be
  skipped
- missing required env or required mount sources may be fatal in explicit
  validation, but must not become hidden preflight gates in ordinary run
- auxiliary descriptor-resolution warnings must not overwrite the real provider
  startup error if the actual run proceeds and fails

Rollback:

- this slice is internal only if it keeps the public `TaskSpec` unchanged
- rollback is clean if the descriptor loader, Docker-runner consumer, docs, and
  `codex` descriptor are reverted together
- leftover warmed Docker images are acceptable rollback residue; do not add
  cleanup complexity to this slice just to make rollback cosmetically perfect

## Assumptions And Open Questions

Assumptions for the first slice:

- `codex` can run in the current Weft Docker-backed lane with a minimal env
  story of `OPENAI_API_KEY` plus an optional read-only `.codex` mount
- `.codex` is config/auth state and should be read-only by default
- no descriptor-driven image-file copying is needed for the first `codex` slice

Open questions that must be resolved with concrete evidence, not guesses:

- Does `codex` need any runtime env beyond `OPENAI_API_KEY` for the supported
  Weft lane?
- Does `codex` try to write into `.codex` in a way that breaks read-only use?
- Does explicit validation have a clean current hook for descriptor-required env
  and mount checks, or should that part wait for a small follow-on slice?

If any answer contradicts the assumptions above, stop and update the plan or
record the deviation explicitly instead of silently widening defaults.

## Tasks

1. Add an internal typed container runtime descriptor model and packaged JSON
   loader.
   - Outcome: Weft has one internal representation for provider container
     runtime requirements, backed by packaged JSON, with a loader that fails
     clearly on malformed assets.
   - Files to touch:
     - `weft/core/agents/provider_cli/container_runtime.py`
     - new packaged data dir:
       `weft/core/agents/provider_cli/runtime_descriptors/`
     - `tests/core/test_provider_cli_container_runtime.py`
   - Read first:
     - `weft/shell/known_interpreters.py`
     - `weft/core/agents/provider_cli/registry.py`
     - `weft/ext.py` for existing typed extension/result patterns
   - Required design:
     - define a versioned descriptor dataclass and loader
     - support only a small, explicit source-kind enum:
       `host_env`, `home_file`, `home_dir`, `project_file`, `project_dir`,
       `task_working_dir`
     - model runtime mounts and env requirements separately
     - model read-only defaults explicitly
   - Do not:
     - add shell snippets
     - add generic command templates
     - add public `TaskSpec` wiring for this
   - Verification:
     - failing test first for malformed descriptor rejection
     - passing tests for packaged descriptor discovery and typed parsing
   - Stop and re-evaluate if:
     - the descriptor starts carrying output parsing or argv templating

2. Add descriptor-resolution helpers for env and runtime mounts.
   - Outcome: Given a provider name, current project/root context, current task
     env, and task working dir, Weft can deterministically compute:
     - env vars to forward
     - mounts to add
     - diagnostics for missing required sources
   - Files to touch:
     - `weft/core/agents/provider_cli/container_runtime.py`
       or a sibling helper module if a small split is truly clearer
     - `tests/core/test_provider_cli_container_runtime.py`
     - `extensions/weft_docker/tests/test_container_runtime_resolution.py`
   - Required merge rules:
     - explicit `spec.env` wins over descriptor-forwarded env
     - explicit `runner.options.mounts` wins over descriptor default mounts at
       the same target path
     - descriptor-generated config mounts default to `read_only=true`
     - missing optional sources are skipped
     - missing required sources are reported through diagnostics, not silently
       ignored
   - Keep real:
     - real path resolution through `Path.home()`, project-root resolution, and
       task working-dir mapping where practical
   - Mock only:
     - isolated temporary home/project directories
   - Verification:
     - tests for present `.codex` -> read-only mount generated
     - tests for absent optional `.codex` -> no mount generated
     - tests for explicit env override precedence
     - tests for explicit mount target override precedence
   - Stop and re-evaluate if:
     - resolution wants to read or mutate unrelated host state

3. Add the first real descriptor for `codex`.
   - Outcome: `codex` has one concrete descriptor asset that expresses only the
     runtime requirements Weft actually intends to support now.
   - Files to touch:
     - `weft/core/agents/provider_cli/runtime_descriptors/codex.json`
     - `tests/core/test_provider_cli_container_runtime.py`
   - Required first-slice contents:
     - provider name: `codex`
     - allowlisted required env: `OPENAI_API_KEY`
     - optional read-only config mount for `.codex`
   - Optional only if proven by current code/docs/manual evidence:
     - additional allowlisted env names
     - additional optional runtime mounts
   - Do not guess a broad `codex` support surface. If a field is not proven,
     leave it out.
   - Verification:
     - descriptor loader test
     - resolution tests for `.codex` and env forwarding
   - Stop and re-evaluate if:
     - `codex` needs a materially broader runtime contract than this plan
       assumes

4. Integrate the descriptor into the Docker-backed one-shot provider run path.
   - Outcome: Docker-backed `provider_cli` runs can pick up provider-wide
     runtime env and config mounts from the descriptor without changing the
     public `TaskSpec`.
   - Files to touch:
     - `extensions/weft_docker/weft_docker/agent_runner.py`
     - `extensions/weft_docker/weft_docker/plugin.py`
     - `extensions/weft_docker/tests/test_agent_runner.py`
     - `extensions/weft_docker/tests/test_docker_plugin.py`
   - Read first:
     - current mount/env merge path in
       `extensions/weft_docker/weft_docker/agent_runner.py`
     - current validation in `extensions/weft_docker/weft_docker/plugin.py`
   - Required behavior:
     - require both image recipe and container runtime descriptor for
       Docker-backed provider support
     - keep the provider-runtime descriptor lookup in core and consume it from
       the Docker package rather than relocating the descriptor into
       `extensions/weft_docker/`
     - merge descriptor-generated mounts/env into the existing container create
       path
     - keep explicit user-provided env/mounts authoritative
     - do not add hidden startup preflight in ordinary run
   - Explicit validation behavior:
     - if the current preflight seam is clean, use the same resolver to report
       missing required env or mount sources only when validation is explicitly
       requested
     - if the seam is not clean, defer that diagnostic behavior rather than
       polluting ordinary execution
   - Verification:
     - focused runner tests for descriptor-provided env and mount merge
     - plugin test for missing descriptor rejection
   - Stop and re-evaluate if:
     - integration wants a second code path for provider env/mount merging

5. Move provider-wide `codex` runtime needs out of the example builtin.
   - Outcome: `example-dockerized-agent` keeps only repo-specific behavior,
     while provider-wide auth/config defaults come from the `codex` descriptor.
   - Files to touch:
     - `weft/builtins/example_dockerized_agent.py`
     - `tests/core/test_builtin_example_dockerized_agent.py`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
   - Required change:
     - remove ad hoc `OPENAI_API_KEY` forwarding from the builtin environment
       profile if the descriptor now owns it
     - keep the example’s repo-specific architecture-doc mount in the builtin
     - keep the example explicit and repo-local; do not turn it into a general
       launcher
   - Verification:
     - update the builtin-support tests to prove the example now contributes
       only the repo-specific mount and network choice
   - Stop and re-evaluate if:
     - the builtin starts re-implementing provider runtime policy again

6. Update current specs and docs so the boundary is explicit.
   - Outcome: the current specs say what the descriptor is, what it owns, and
     what it does not own.
   - Files to touch:
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/13A-Agent_Runtime_Planned.md`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `docs/specifications/02-TaskSpec.md`
     - `README.md` only if one short note is needed
   - Required documentation points:
     - the Docker-backed `provider_cli` lane may use internal provider
       container runtime descriptors
     - the descriptor owns runtime requirements such as allowlisted env and
       runtime mounts
     - provider adapter code still owns CLI invocation building
     - config dirs like `.codex` are runtime-mounted, not copied into the image
     - no public `TaskSpec` change lands in this slice
   - Verification:
     - doc sections and implementation mappings point at the real owning files
     - each touched spec gets a backlink to this plan

## Verification Plan

Use red-green TDD where the behavior is crisp.

Required automated checks after implementation:

- `./.venv/bin/python -m pytest tests/core/test_provider_cli_container_runtime.py`
- `./.venv/bin/python -m pytest extensions/weft_docker/tests/test_container_runtime_resolution.py`
- `./.venv/bin/python -m pytest extensions/weft_docker/tests/test_agent_runner.py`
- `./.venv/bin/python -m pytest extensions/weft_docker/tests/test_docker_plugin.py`
- `./.venv/bin/python -m pytest tests/core/test_agent_validation.py`
- `./.venv/bin/python -m pytest tests/core/test_builtin_example_dockerized_agent.py`
- `./.venv/bin/python -m pytest tests/system/test_builtin_contract.py tests/cli/test_cli_spec.py`
- `./.venv/bin/ruff check` on touched files
- `./.venv/bin/mypy` on touched product files

Optional manual smoke if Docker and auth are available:

- `printf '{"template":"explain"}\n' | weft run --spec example-dockerized-agent`

What not to mock:

- do not mock away the provider adapter when testing merge semantics around the
  Docker runner if the real `CodexProvider` can be exercised without network
- do not mock away `TaskRunner` just to prove descriptor merging; test the
  actual Docker-runner-facing seam where practical
- it is acceptable to use fake Docker SDK clients in focused extension tests,
  because Docker is an external boundary

## Review Loop

Independent review is required before implementation and again after the first
meaningful code slice because this change creates a new reusable internal
contract.

At execution time:

1. check which reviewer families are actually available
2. prefer a different agent family than the implementing agent
3. use the plan-review prompt from
   `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
4. treat "I could not implement this confidently" as a blocker

## Success Criteria

The slice is complete only when all of the following are true:

- `codex` has both:
  - a Docker image recipe
  - a container runtime descriptor
- Docker-backed one-shot `codex` runs can pick up optional read-only `.codex`
  runtime mounts and allowlisted env forwarding without a public `TaskSpec`
  change
- `example-dockerized-agent` no longer owns provider-wide auth wiring
- current specs describe the descriptor boundary clearly
- normal `weft run` still attempts the real run instead of adding hidden
  readiness gates

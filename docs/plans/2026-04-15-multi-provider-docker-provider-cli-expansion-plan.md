# Multi-Provider Docker `provider_cli` Expansion Plan

## Goal

Expand the current Docker-backed one-shot delegated `provider_cli` lane from
`codex` only to all provider CLIs that Weft can currently detect with
`probe-agents`: `claude_code`, `codex`, `gemini`, `opencode`, and `qwen`.
Generalize the current `codex`-only container runtime metadata into an
explicit internal provider-runtime metadata system that is still Weft-like:
explicit, narrow, and runner-owned where it should be. The release gate is
real end-to-end Docker-backed one-shot execution for each supported provider,
not just host probing.

This plan is deliberately opinionated about boundary shape:

- keep provider invocation building and result parsing in code
- keep Docker image install/build/run logic in the Docker package
- move only provider runtime metadata and shared runtime-prep helpers into core
- do not mutate one stored TaskSpec's provider at ordinary run time
- do not make host-binary mounting the default or supported release path

The strongest counterargument is the selector UX: it would be nice if one spec
could take `--provider` and maybe `--model`. That is convenience, not the core
support contract. Doing it first would hide the real work under a wrapper and
encourage mutating `spec.agent.runtime_config.provider` at submission time.
That is the wrong order. First make each provider actually work in Docker.
Then decide whether a thin explicit selector helper is worth adding above that.

## Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1.3]
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-0.1], [AR-2.1], [AR-5], [AR-7]
- [`docs/specifications/13A-Agent_Runtime_Planned.md`](../specifications/13A-Agent_Runtime_Planned.md)
  [AR-A1], [AR-A3], [AR-A4], [AR-A5]

Current related plans:

- [`docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](./2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md)
- [`docs/plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md`](./2026-04-14-provider-cli-container-runtime-descriptor-plan.md)
- [`docs/plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`](./2026-04-15-spec-run-input-adapter-and-declared-args-plan.md)

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)

External technical references used to verify unstable provider install surfaces:

- [Anthropic Claude Code setup](https://docs.anthropic.com/en/docs/claude-code/getting-started)
- [Anthropic Claude Code quickstart](https://docs.anthropic.com/en/docs/claude-code/quickstart)
- [Anthropic Claude Code troubleshooting](https://docs.anthropic.com/en/docs/claude-code/troubleshooting)

Source-spec gap:

- The current specs describe the `provider_cli` Docker-backed lane and the
  current `codex` descriptor, but they do not yet specify the richer internal
  provider-runtime metadata needed for all detected CLIs.
- The current specs also lag live adapter reality for model support. Today
  `claude`, `codex`, and `qwen` all expose model flags in their live CLIs, but
  current spec text says only `gemini` and `opencode` support
  `spec.agent.model`.
- This slice is not done until the touched current-state specs describe the
  new metadata boundary and the corrected model-support matrix.

## Context and Key Files

Files to modify:

- core provider runtime metadata and helpers:
  - `weft/core/agents/provider_cli/container_runtime.py`
  - `weft/core/agents/provider_cli/runtime_descriptors/`
  - recommended new helper module if needed:
    `weft/core/agents/provider_cli/runtime_prep.py`
- provider adapter code:
  - `weft/core/agents/provider_cli/registry.py`
  - `weft/core/agents/provider_cli/execution.py` only if a shared prep seam is
    needed for Docker plus host reuse
  - `weft/core/agents/validation.py`
- Docker package:
  - `extensions/weft_docker/weft_docker/agent_images.py`
  - `extensions/weft_docker/weft_docker/agent_runner.py`
  - `extensions/weft_docker/weft_docker/plugin.py`
- builtins:
  - `weft/builtins/agent_probe.py`
  - `weft/builtins/agent_images.py`
  - `weft/builtins/tasks/example-dockerized-agent/`
  - likely new smoke helpers or example bundles under `weft/builtins/tasks/`
- docs:
  - `docs/specifications/13-Agent_Runtime.md`
  - `docs/specifications/13A-Agent_Runtime_Planned.md`
  - `docs/specifications/10B-Builtin_TaskSpecs.md`
  - `docs/specifications/02-TaskSpec.md`
  - `README.md`
- tests:
  - `tests/core/test_provider_cli_container_runtime.py`
  - `tests/core/test_agent_validation.py`
  - `tests/core/test_agent_runtime.py` if runtime normalization or execution
    ownership changes
  - `extensions/weft_docker/tests/test_agent_runner.py`
  - `extensions/weft_docker/tests/test_docker_plugin.py`
  - `extensions/weft_docker/tests/test_agent_images.py`
  - `tests/core/test_builtin_example_dockerized_agent.py`
  - `tests/system/test_builtin_contract.py`

Read first:

1. `docs/specifications/13-Agent_Runtime.md` [AR-0.1], [AR-2.1], [AR-7]
2. `docs/specifications/02-TaskSpec.md` [TS-1.3]
3. `docs/specifications/10B-Builtin_TaskSpecs.md`
4. `weft/core/agents/provider_cli/registry.py`
5. `weft/core/agents/provider_cli/container_runtime.py`
6. `extensions/weft_docker/weft_docker/agent_runner.py`
7. `extensions/weft_docker/weft_docker/agent_images.py`
8. `weft/builtins/agent_probe.py`
9. `weft/builtins/tasks/example-dockerized-agent/`
10. `tests/core/test_provider_cli_container_runtime.py`
11. `extensions/weft_docker/tests/test_agent_runner.py`

Shared paths and helpers to reuse:

- `weft/core/agents/provider_cli/registry.py` remains the owner of:
  - exact CLI argv construction
  - provider option validation
  - bounded/general semantics
  - model support truth
  - result parsing
- `weft/core/agents/provider_cli/container_runtime.py` remains the owner of:
  - provider container runtime metadata parsing
  - runtime source resolution
  - explicit diagnostics for validation
- `extensions/weft_docker/weft_docker/agent_runner.py` remains the owner of:
  - actual Docker container creation
  - bind mounts
  - env injection
  - tempdir lifecycle
  - cancellation/timeout/container cleanup
- `extensions/weft_docker/weft_docker/agent_images.py` remains the owner of:
  - Docker image recipes
  - image cache keys
  - image ensure/build policy

Current structure:

- `probe-agents` can detect all five providers on this machine today.
- The Docker-backed one-shot lane is real current behavior, but only `codex`
  has both an image recipe and a container runtime descriptor today.
- The current runtime descriptor system is too small. It only supports
  host-env forwarding and bind mounts.
- `gemini` already has provider-specific runtime prep in code:
  `_build_gemini_session_environment()` writes a minimal isolated home and
  copies a subset of auth files. That is proof that container support needs
  richer runtime-prep semantics than `codex` currently uses.
- Current model-support metadata is stale relative to live CLIs.

Comprehension questions before editing:

1. Which file currently owns the exact provider CLI invocation shape for
   `codex exec`, `claude -p`, `gemini -p`, `opencode run`, and `qwen -p`?
   Answer: `weft/core/agents/provider_cli/registry.py`.
2. Which layer currently owns Docker container creation and mount/env merging?
   Answer: `extensions/weft_docker/weft_docker/agent_runner.py`.
3. Which helper currently proves that some providers need more than bind mounts
   and env forwarding?
   Answer: `_build_gemini_session_environment()` in
   `weft/core/agents/provider_cli/registry.py`.
4. Which path must remain the only execution spine?
   Answer:
   `TaskSpec -> Manager -> Consumer -> TaskRunner -> runner plugin -> queues/state log`.

## Invariants and Constraints

These are hard constraints for this slice:

1. No second execution path.
   All support must stay on the normal durable spine. No sidecar daemon, no
   hidden agent manager, no separate container orchestration path.
2. No hidden readiness preflight on ordinary run.
   Normal `weft run` must still attempt the real run. Validation and builtins
   may diagnose.
3. Provider invocation logic stays in code.
   Do not move exact CLI argv or parse behavior into JSON.
4. Container install/build logic stays in the Docker package.
   Do not move image recipes or Docker SDK calls into core.
5. Provider runtime metadata lives in core.
   The metadata is about provider runtime needs, not about Docker alone.
6. Container is the outer security boundary for Docker-backed delegated runs.
   Do not assume inner provider sandboxing is the primary isolation boundary.
   Where a provider needs different Docker-lane defaults because of this, keep
   that logic in provider adapter code or a small code-owned provider policy
   layer, not in JSON.
7. Network defaults on for hosted delegated CLIs.
   Do not design this slice around `network=none` as the normal mode.
8. User-authored runtime values still win.
   Explicit `spec.env` and explicit runner mounts/options override descriptor
   defaults. Descriptor defaults fill gaps; they do not silently replace user
   policy.
9. Avoid host-binary mount as the product path.
   It is non-portable, not reproducible, and fails outright for native binaries
   like the local `claude` install on macOS. Do not design around it.
10. Prefer explicit, deterministic auth/config reuse.
   Do not slurp all of `os.environ`. Use provider-scoped allowlists, explicit
   mounts, or explicit file copies into isolated runtime homes.
11. Keep tests real where practical.
   Use real descriptor parsing and real Docker-runner prep seams. Do not mock
   away the core resolution path.
12. No speculative cross-runner abstraction for provider selection.
   Runtime provider selection inside one stored TaskSpec is not part of this
   release slice.

Stop-and-re-evaluate gates:

- you are about to encode full provider command templates in JSON
- you are about to mutate `spec.agent.runtime_config.provider` inside a normal
  `weft run --spec` path
- you are about to solve provider support by mounting host binaries as the
  primary path
- you are adding a large generic “generated file DSL” to descriptor JSON
- the test strategy becomes mock-heavy because the real runner path feels
  inconvenient
- a provider cannot be given a deterministic Linux image install path
- a provider can only run in Docker by depending on broad ambient host state
  you cannot name explicitly

## Live Audit: What Each Provider Needs

This section is the most important part of the plan. It is the current audit of
what metadata and code work are needed for each detected CLI.

### Shared metadata categories

The current `codex` descriptor only expresses:

- bind mounts
- host env forwarding

That is not enough. A provider-ready metadata system must cover at least:

- image recipe presence and install path owner
- runtime auth strategy
  - host env forward
  - bind mount
  - copy selected files into isolated runtime home
- runtime state strategy
  - pass-through mounted host state
  - isolated generated home with copied auth subset
  - writable scratch dirs
- target paths in container
- required vs optional sources
- read-only vs writable semantics
- explicit diagnostics when a declared source cannot be resolved

What must stay out of the descriptor:

- exact CLI argv
- output parsing
- provider option conflict logic
- bounded/general authority logic
- model support truth

### `codex`

Current evidence:

- install path: Bun global package `@openai/codex`; current Docker recipe
  already uses npm install in Linux
- auth/config state: `~/.codex/auth.json`, `~/.codex/config.toml`, plus runtime
  sqlite/log/state files
- current successful Docker support uses a writable `~/.codex` mount and does
  not auto-forward ambient `OPENAI_API_KEY`
- live CLI exposes `-m/--model`, but current adapter does not mark model
  support

Metadata/code needed:

- image recipe: keep in Docker package; already exists
- runtime profile:
  - writable bind mount for `~/.codex` at `/root/.codex`
  - no default ambient host `OPENAI_API_KEY` forward
- adapter correction:
  - mark model override support and pass `--model`
- likely Docker-lane provider policy:
  - keep “outer container is the boundary” semantics explicit; do not force the
    example-only workaround to carry permanent product policy

Risk:

- mounting the full writable `~/.codex` tree is pragmatic but broad. Accept it
  for now because it works and is explicit. Do not pretend it is a minimal auth
  footprint.

### `gemini`

Current evidence:

- install path: Bun global package `@google/gemini-cli`
- auth/state lives in `~/.gemini`
- current host-backed Weft runtime already uses `_build_gemini_session_environment()`
  to:
  - create isolated home
  - write minimal `settings.json`, `projects.json`, `trustedFolders.json`
  - copy only `google_accounts.json`, `installation_id`, and `state.json`
- live CLI exposes `-m/--model`; current adapter already supports it

Metadata/code needed:

- image recipe in Docker package
- shared runtime-prep abstraction for isolated provider homes:
  - set `HOME` and `USERPROFILE`
  - create provider home tree
  - copy selected auth files from host
  - seed generated minimal settings files
- move or refactor the existing Gemini-specific prep so Docker and host can
  share the same truth instead of forking similar logic

Risk:

- if the refactor tries to “generalize all providers” too early, it will get
  abstract and brittle. Start by extracting only the seams Gemini and Docker
  actually need.

### `qwen`

Current evidence:

- install path: Bun global package `@qwen-code/qwen-code`
- host auth works via `qwen auth status`
- likely auth/state files live in `~/.qwen`, including `oauth_creds.json`,
  `installation_id`, `settings.json`, plus writable debug/projects/tmp/todos
- live CLI exposes `-m/--model`, but current adapter does not mark model
  support
- current adapter supports bounded authority and read-only workspace

Metadata/code needed:

- image recipe in Docker package
- adapter correction:
  - mark model override support and pass `--model`
- runtime profile decision:
  - either mount writable `~/.qwen`
  - or create isolated runtime home and copy only auth-critical files plus
    seed writable runtime dirs

Decision rule:

- prefer isolated-home copy only if it is proven with a live Docker smoke run
  and the required file set is small and stable
- otherwise use explicit writable `~/.qwen` mount first, document it, and
  tighten later

Risk:

- current host success is real, but the minimal auth/runtime file set is not
  yet proven. Do not guess. Spike it first.

### `claude_code`

Current evidence:

- local install is a native macOS binary under `~/.local/share/claude/versions`,
  which proves host-binary mount is the wrong default path
- official Anthropic docs say Claude Code supports Linux and can be installed
  with `npm install -g @anthropic-ai/claude-code`; native Linux install also
  exists
- host auth works via `claude auth status`
- host state likely spans `~/.claude` and `~/.claude.json`
- live CLI exposes `--model`
- `--bare` exists, but it explicitly disables OAuth/keychain state and expects
  API-key-based auth instead

Metadata/code needed:

- image recipe in Docker package:
  - prefer official npm install in Linux image
  - native installer is fallback only if npm path proves broken in container
- adapter correction:
  - mark model override support and pass `--model`
- runtime profile decision:
  - likely mount or copy explicit Claude auth/config state
  - do not switch to `--bare` unless the release story is willing to require
    explicit `ANTHROPIC_API_KEY` instead of current host OAuth state

Decision rule:

- release support must be based on a live Docker smoke run using the auth mode
  Weft actually supports
- if the only clean Docker path is `--bare` plus explicit API key env, then
  either:
  - accept that as the supported Docker auth mode and document that host OAuth
    state is not reused, or
  - do the extra work to explicitly mount/copy the auth state that actually
    works
- do not hand-wave this difference

Risk:

- Claude is the shakiest provider in this slice because the install surface and
  auth-state reuse story differ the most from the current local machine setup.

### `opencode`

Current evidence:

- install path: Bun global package `opencode-ai`
- live non-interactive `run` works
- `opencode providers list` reports:
  - zero stored credentials in `~/.local/share/opencode/auth.json`
  - environment-variable auth options for OpenAI, OpenRouter, Gemini, GitHub,
    xAI
- host state exists under `~/.local/state/opencode`,
  `~/.local/share/opencode`, and `~/.cache/opencode`
- current host success in this environment is very likely using ambient
  `OPENAI_API_KEY`
- live CLI exposes `-m/--model`; adapter already supports it

Metadata/code needed:

- image recipe in Docker package
- runtime env allowlist for explicit env-based auth
- runtime state decision:
  - likely optional or unnecessary for one-shot execution if model is explicit
  - do not make hidden host state reuse the default unless a live Docker spike
    proves it is required
- release smoke should pass an explicit model so success does not depend on a
  hidden host default model choice

Risk:

- opencode is the cleanest argument for env-based auth defaults, but that
  clashes with the stricter explicitness we adopted for `codex`. Be honest
  about the tradeoff. If this slice chooses env allowlists for env-first CLIs,
  document that split clearly instead of pretending all providers work the same
  way.

## Design Position

The right shape is a three-layer split:

1. provider adapter code in `registry.py`
   - owns invocation, parse, authority, and model support
2. provider runtime metadata in core
   - owns runtime auth/config/state requirements
3. Docker package
   - owns image recipes and actual container build/run mechanics

Do not collapse those layers.

### Descriptor direction

Generalize the current descriptor model, but do not turn it into a scripting
language.

The likely minimum new runtime-prep concepts are:

- bind mounts
- host env forwards
- copy selected files
- maybe copy selected dirs if proven necessary
- isolated runtime home root
- create writable dirs

Recommended shape:

- keep a typed JSON-backed descriptor for declarative facts
- add a small code-owned runtime prep layer for provider-specific generated
  state that should not live in JSON

Examples:

- `codex` can remain descriptor-only
- `gemini` needs descriptor data plus code-owned seeded files for its isolated
  home
- `qwen` and `claude_code` may need the same split depending on what the live
  Docker spike proves

Do not build a generic JSON templating language for generated files.

### Selector UX

Do not make “one spec chooses the provider at run time” the release shape.

Why not:

- `spec.agent.runtime_config.provider` is part of the spec-owned immutable
  execution contract
- mutating it at `weft run --spec` time would blur the spec boundary
- it would make the selector wrapper look finished before the providers are
  actually supported

What to do instead in this slice:

- support each provider explicitly
- expose model support correctly in provider adapters
- if a convenience helper is added at all, make it an explicit local wrapper
  that selects a concrete provider-backed spec before submission, not a hidden
  mutation inside one stored TaskSpec

This selector helper is a follow-on, not the release gate.

## Release Gate

This slice is not done until all of the following are true:

1. `probe-agents` still detects the current providers correctly.
2. Each shipped supported provider has:
   - a Docker image recipe
   - a provider runtime descriptor
   - any required code-owned runtime prep helper
   - a passing Docker-backed one-shot smoke path
3. Current-state specs describe:
   - the richer provider runtime metadata boundary
   - the corrected model-support matrix
   - the builtins/examples used to exercise the lane
4. The release smoke commands work against the same input document across all
   providers and return one sentence of explanation.
5. The Docker-backed provider lane remains one-shot only and does not add a
   second control plane.

Manual smoke gate:

- a built-in or explicit command path for each provider must succeed in a live
  Docker-backed run against
  `docs/specifications/00-Overview_and_Architecture.md`
- use the same semantic task:
  “Explain this document in one sentence.”
- where possible, pass explicit model names to avoid accidental dependence on
  host default-model state

If any provider cannot meet that gate because the install path or auth story
cannot be made explicit and reproducible, stop and narrow the release scope in
the plan before merging code.

## Tasks

1. Correct the provider capability truth before touching Docker support.
   - Outcome: the adapter registry truth matches the real CLI surfaces for
     model support and any other now-stale capability claims.
   - Files to touch:
     - `weft/core/agents/provider_cli/registry.py`
     - `tests/core/test_agent_validation.py`
     - provider adapter tests wherever model support is currently asserted
   - Read first:
     - live CLI help for `claude`, `codex`, `qwen`
     - `docs/specifications/13-Agent_Runtime.md` [AR-2.1]
   - Changes:
     - add `supports_model` and `--model` handling for `claude_code`,
       `codex`, and `qwen`
     - keep `gemini` and `opencode` support
     - update spec text accordingly
   - Tests:
     - validation tests for `spec.agent.model`
     - invocation construction tests proving the right `--model` flag is emitted
   - Stop gate:
     - if this task starts changing runtime descriptor ownership, stop; this is
       an adapter-capability correction only
   - Done signal:
     - provider capability tests pass and spec text matches live CLI support

2. Expand the provider runtime descriptor model from `codex`-only mount/env to
   richer runtime prep.
   - Outcome: core can describe and resolve the runtime auth/config/state needs
     that all providers need for Docker-backed execution.
   - Files to touch:
     - `weft/core/agents/provider_cli/container_runtime.py`
     - `weft/core/agents/provider_cli/runtime_descriptors/`
     - recommended new helper:
       `weft/core/agents/provider_cli/runtime_prep.py`
     - `tests/core/test_provider_cli_container_runtime.py`
   - Read first:
     - current `codex` descriptor
     - `_build_gemini_session_environment()` in `registry.py`
   - Required design:
     - support declarative:
       - env forwards
       - bind mounts
       - file copies
       - optional dir copies only if proven necessary
       - isolated runtime home declarations
       - writable dir creation
     - keep generated-file templates out of JSON
     - if the schema becomes materially different, bump descriptor version
       cleanly instead of carrying two half-overlapping shapes forever
   - Tests:
     - parse and validate each descriptor
     - resolve missing/optional sources with explicit diagnostics
     - isolated-home prep tests using real temp dirs, not mocks
   - Stop gate:
     - if you are inventing a generic file-templating DSL, stop and replace it
       with a small code-owned strategy hook
   - Done signal:
     - descriptor resolution can express all five providers’ known needs

3. Extract shared runtime-prep helpers for providers that need isolated homes.
   - Outcome: runtime prep that currently exists only as Gemini host-session
     code becomes shared truth that Docker can reuse.
   - Files to touch:
     - `weft/core/agents/provider_cli/registry.py`
     - `weft/core/agents/provider_cli/runtime_prep.py`
     - possibly `weft/core/agents/provider_cli/execution.py`
     - tests for Gemini and any second provider that uses isolated homes
   - Read first:
     - `_build_gemini_session_environment()`
     - host one-shot and persistent Gemini paths in `registry.py`
   - Changes:
     - move shared file-copy and home-seeding logic out of ad hoc provider code
       only where reuse is real
     - keep host behavior intact
   - Tests:
     - host Gemini behavior remains unchanged
     - Docker prep can consume the same source truth
   - Stop gate:
     - if this becomes a large provider-runtime framework, stop and narrow it
       back to the smallest reusable seam
   - Done signal:
     - Gemini no longer has one-off runtime-prep truth duplicated in two places

4. Run live Docker strategy spikes for the uncertain providers before broad
   implementation.
   - Outcome: the plan for `claude_code`, `qwen`, and `opencode` is based on
     actual Linux-container evidence rather than host inference.
   - Files to touch:
     - none in product code first
     - update this plan or implementation notes if the spike changes a decision
   - Providers to spike:
     - `claude_code`
     - `qwen`
     - `opencode`
   - Required questions:
     - can the provider be installed reproducibly in a Linux image?
     - what is the smallest explicit auth/config/runtime state that makes a
       one-shot Docker run work?
     - can explicit model selection remove hidden dependence on host default
       model state?
     - does the provider need a writable runtime home, or is copied auth plus
       scratch dirs enough?
   - Acceptable outputs:
     - “writable host mount first”
     - “isolated home with copied auth subset”
     - “explicit env auth only”
   - Unacceptable output:
     - “it seems like this should work”
   - Tests:
     - this is a live spike, not a unit-test task
     - capture the chosen strategy in code comments or docs when you implement
       it
   - Stop gate:
     - if a provider cannot be made explicit after a real spike, narrow the
       scope before continuing
   - Done signal:
     - each uncertain provider has one chosen runtime strategy with evidence

5. Add Docker image recipes for all target providers.
   - Outcome: every supported provider has a deterministic Linux image recipe.
   - Files to touch:
     - `extensions/weft_docker/weft_docker/agent_images.py`
     - `extensions/weft_docker/tests/test_agent_images.py`
   - Read first:
     - `codex` image recipe
     - provider install docs or local packaging evidence
   - Required image recipes:
     - `claude_code`
     - `codex`
     - `gemini`
     - `opencode`
     - `qwen`
   - Constraints:
     - install the provider in the image, do not mount host binaries
     - prefer straightforward Linux-friendly install paths
     - document any provider that needs a different runtime base or extra
       packages
   - Tests:
     - recipe-registry tests asserting every supported provider has a recipe
     - cache-key tests
   - Stop gate:
     - if a provider cannot be installed reproducibly in Linux, stop and
       explicitly revisit release scope
   - Done signal:
     - `prepare-agent-images` can report support for all target providers

6. Add per-provider runtime descriptors and any small provider-specific prep
   hooks they require.
   - Outcome: every supported provider has explicit container runtime metadata.
   - Files to touch:
     - `weft/core/agents/provider_cli/runtime_descriptors/*.json`
     - `weft/core/agents/provider_cli/runtime_prep.py`
     - `tests/core/test_provider_cli_container_runtime.py`
   - Expected first descriptor set:
     - `claude_code.json`
     - `codex.json`
     - `gemini.json`
     - `opencode.json`
     - `qwen.json`
   - Provider expectations:
     - `codex`: writable `.codex` mount
     - `gemini`: isolated home plus copied auth subset and generated minimal
       settings
     - `opencode`: env allowlist, maybe no host-state dependency if explicit
       model is supplied
     - `qwen`: spike-driven choice between writable mount and isolated home
     - `claude_code`: spike-driven auth-state reuse vs explicit env auth mode
   - Tests:
     - descriptor loading for each provider
     - minimal source resolution diagnostics
   - Stop gate:
     - if a provider descriptor starts including exact CLI flags, move that
       logic back to adapter code
   - Done signal:
     - every provider has explicit runtime metadata with passing parse tests

7. Extend the Docker runner to apply the richer runtime-prep model.
   - Outcome: Docker-backed one-shot provider runs can use bind mounts, copied
     files, isolated homes, writable runtime dirs, and provider env defaults.
   - Files to touch:
     - `extensions/weft_docker/weft_docker/agent_runner.py`
     - `extensions/weft_docker/weft_docker/plugin.py`
     - `extensions/weft_docker/tests/test_agent_runner.py`
     - `extensions/weft_docker/tests/test_docker_plugin.py`
   - Read first:
     - current mount/env merge behavior in `agent_runner.py`
   - Changes:
     - resolve descriptor runtime inputs
     - create any needed temp runtime home inside the run tempdir
     - copy selected files before container start
     - mount temp runtime home into the container when needed
     - set `HOME` and `USERPROFILE` when required
     - keep explicit user env and explicit mounts authoritative
   - Tests:
     - runner prep tests for:
       - bind mount only
       - isolated home with copied files
       - missing required source diagnostics in validation path
       - precedence of explicit env/mounts over defaults
   - Stop gate:
     - if this starts moving invocation logic into the runner, stop
   - Done signal:
     - runner can prepare and launch all provider runtime shapes without
       special ad hoc branches scattered around the file

8. Generalize the builtins and examples without lying about the shape.
   - Outcome: builtins report and exercise real multi-provider Docker support.
   - Files to touch:
     - `weft/builtins/agent_probe.py`
     - `weft/builtins/agent_images.py`
     - `weft/builtins/tasks/`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `tests/system/test_builtin_contract.py`
   - Required builtin work:
     - `probe-agents` should surface enough metadata to explain Docker support
       gaps cleanly
     - `prepare-agent-images` should build/reuse all actually supported
       provider images
     - add explicit smoke/example specs or a builtin smoke helper for all
       providers; do not fake multi-provider support through one mutable spec
   - Recommendation:
     - use provider-specific example or smoke bundles first
     - if a generic smoke helper is added, let it choose a concrete provider
       spec before submission rather than mutating the spec in place
   - Tests:
     - builtin inventory
     - builtin contract tests
     - smoke helper input-shaping tests if added
   - Stop gate:
     - if a single builtin spec starts mutating its provider at ordinary run
       time, stop and split that convenience into a separate plan
   - Done signal:
     - builtins and examples match the real provider support matrix

9. Update current-state specs and README before declaring the slice done.
   - Outcome: docs describe the actual boundary, provider support, and release
     shape.
   - Files to touch:
     - `docs/specifications/13-Agent_Runtime.md`
     - `docs/specifications/13A-Agent_Runtime_Planned.md`
     - `docs/specifications/10B-Builtin_TaskSpecs.md`
     - `docs/specifications/02-TaskSpec.md`
     - `README.md`
   - Required doc deltas:
     - richer provider runtime metadata boundary
     - corrected model-support matrix
     - current supported Docker-backed providers
     - any provider-specific auth/config caveats that matter operationally
     - explicit note that provider-selection convenience in one stored spec is
       not part of the current contract
   - Tests:
     - none beyond doc review, but do not skip this task
   - Done signal:
     - docs, code, and builtins tell the same story

10. Run the full verification set plus live provider smokes.
   - Outcome: code is green locally and the release gate is proven with the
     real CLIs.
   - Automated verification:
     - `./.venv/bin/pytest -n0`
     - `./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
     - `./.venv/bin/ruff check weft extensions tests`
     - `./.venv/bin/ruff format --check weft extensions tests`
   - Live manual verification:
     - `./.venv/bin/python -m weft.cli run --spec probe-agents`
     - `./.venv/bin/python -m weft.cli run --spec prepare-agent-images`
     - one Docker-backed one-shot run per provider against the same document
   - Suggested manual smoke invariant:
     - every provider returns one sentence
     - container networking remains enabled
     - provider auth/config is sourced only from the declared descriptor/runtime
       prep story
   - Stop gate:
     - if a provider only works because of undeclared ambient host state, the
       release gate is not met

## Testing Guidance

Keep these real:

- descriptor parsing and resolution
- file copy and isolated-home tempdir prep
- Docker runner env and mount assembly
- builtin example/spec loading
- full local `pytest -n0`, `mypy`, `ruff check`, and `ruff format --check`

Mock only:

- Docker SDK client calls when proving image lookup/build decision logic in
  unit tests
- provider subprocess execution in pure adapter tests when only argv shape is
  under test

Do not build this slice out of mocks for:

- descriptor resolution
- tempdir/runtime-home setup
- builtin spec loading
- the full end-to-end release smoke gate

## Rollout and Rollback

Rollout order:

1. correct adapter capability truth
2. land descriptor/runtime-prep model
3. add provider image recipes
4. add provider runtime descriptors
5. wire Docker runner to the richer prep
6. land builtins/examples
7. update specs/docs
8. run full verification and live smokes

Rollback rule:

- if a provider-specific support path is not real, remove or mark that provider
  unsupported in image recipes/builtins/docs rather than shipping an optimistic
  partial path
- do not leave spec text claiming support for a provider whose live Docker
  smoke is not passing

One-way door warning:

- broadening the descriptor schema is a one-way complexity door. Keep it small.
- adding runtime-provider selection inside one stored spec would also be a
  one-way public-contract door. Do not do it in this slice.

## Why This Is The Right Scope

The steelman alternative is to jump straight to a generic selector UX:
`weft run --spec docker-agent --provider gemini --model ...`.

That is attractive, but it is the wrong first move for Weft:

- it hides the real install/auth/runtime work
- it pressures the spec boundary to become mutable at submission time
- it makes the wrapper look finished even if one or more providers are not
  actually Docker-ready

The weaker alternative is to keep hand-written one-off branches for each
provider.

That is also wrong:

- it duplicates auth/runtime prep logic
- it obscures what metadata each provider truly needs
- it makes docs and validation drift

This plan is the narrow middle:

- explicit provider metadata in core
- explicit provider invocation logic in code
- explicit Docker runner/image support in the Docker package
- explicit release gate based on real runs, not probe optimism

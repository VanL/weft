# Builtin TaskSpecs

This document defines the current contract for builtin TaskSpecs shipped with
Weft source today. More generally, a builtin is an example task or pipeline
that is useful enough that Weft bundles it directly. The current builtins are
read-only task helpers that live in the Weft package rather than in `.weft/`.
They are resolved only through explicit spec surfaces such as
`weft run --spec NAME|PATH` and `weft spec show NAME`. They are discovered as
shipped inventory through `weft system builtins`.

See also:

- [`10-CLI_Interface.md`](10-CLI_Interface.md)
- [`11-CLI_Architecture_Crosswalk.md`](11-CLI_Architecture_Crosswalk.md)
- [`13-Agent_Runtime.md`](13-Agent_Runtime.md)

## Current Contract

- builtin task specs are shipped as read-only package data under
  `weft/builtins/tasks/*.json` or
  `weft/builtins/tasks/<name>/taskspec.json`
- builtin task specs are task-only; Weft does not currently ship builtin
  pipeline specs
- explicit task-spec lookup follows this order:
  1. existing file path
  2. existing spec-bundle directory path containing `taskspec.json`
  3. local stored task spec in `.weft/tasks/<name>.json`
  4. local stored task bundle in `.weft/tasks/<name>/taskspec.json`
  5. builtin task spec fallback in `weft/builtins/tasks/<name>.json` or
     `weft/builtins/tasks/<name>/taskspec.json`
- local stored task specs or task bundles in `.weft/tasks/` shadow builtin
  task specs with the same name
- builtin task specs are listed by `weft spec list`
- `weft spec list --json` reports builtin entries with `source: "builtin"`
- plain `weft spec list` labels builtin entries with the `(builtin)` suffix
- builtin task specs can be shown by `weft spec show` and executed by
  `weft run --spec NAME|PATH`
- builtin task specs also participate in spec-aware
  `weft run --spec NAME|PATH --help` rendering when they declare submission-time
  options
- `weft system builtins` reports the builtin inventory Weft ships, regardless
  of local `.weft/tasks/` shadows
- builtin-only task specs cannot be deleted through `weft spec delete`
- builtin task specs do not change bare command execution; they are resolved
  only under explicit spec-management or `--spec` surfaces
- builtin task assets must ship with the installed package and be discoverable
  through `weft.builtins`
- bundle entry files use the fixed filename `taskspec.json`; the bundle
  directory name is the public spec name
- when a builtin task spec is loaded from a bundle directory, Python callable
  refs in that spec keep the normal `module:function` form but resolve
  `module` against the bundle root first before falling back to normal Python
  imports
- bundle-local Python helper code should use package-relative imports for
  sibling modules; Weft does not add the bundle root to process-global
  `sys.path`
- each shipped builtin must have a dedicated section in this document
- builtins are explicit, optional helpers. They may smooth a real execution or
  runtime-preparation path, but they are never required for correctness and are
  never run implicitly
- builtins are bundled examples of using Weft as a task runner; they should
  demonstrate the ordinary task or pipeline model rather than bypass it
- projects can apply the same pattern locally through `.weft/tasks/` and
  `.weft/pipelines/`, building reusable task and pipeline libraries over time
  through the same explicit spec surfaces that expose builtins
- builtins may report availability, prepare a slow runner path, or demonstrate
  a Weft workflow, but they must not become a second control plane for agent
  setup or lifecycle management
- service-style builtins may demonstrate named persistent-task patterns only if
  they remain ordinary tasks on the existing durable spine and treat any named
  endpoint or request envelope as explicit optional helper behavior rather than
  as hidden runtime magic
- service-style builtins must not rely on direct backend-specific SQL tables
  inside the broker store as their blessed persistence model; if they need
  durable domain state beyond runtime-only Weft registries, that state belongs
  in an explicit app/domain store outside Weft core truth surfaces
- a service-style builtin may offer stricter inbox schemas, request envelopes,
  or explicit claim and release helpers, but those semantics stay local to the
  builtin contract rather than redefining Weft globally

## Adding A Builtin

Builtin TaskSpecs are part of the public extension contract. New builtins
should be added only when they meet all of these rules:

- the builtin remains an ordinary TaskSpec on the existing durable spine
- the builtin is useful enough to justify a stable public name
- the builtin stays explicit; it must not add bare-command lookup or hidden
  startup behavior
- the builtin should smooth a real Weft execution path or serve as a bundled
  example of using Weft as a task runner, not as a general setup wizard
- if the builtin mutates project-scoped settings, that mutation must be
  explicit in the builtin's contract, narrowly scoped, preserve existing
  user-authored settings where possible, and remain unnecessary for
  correctness
- the builtin asset lives under `weft/builtins/tasks/`
- the builtin has a dedicated section in this document
- the builtin has black-box coverage for resolution and any public behavior it
  introduces
- if the builtin demonstrates a long-lived service/actor pattern, the builtin
  must keep payload and request semantics explicit in its own contract instead
  of implicitly redefining the global Weft task model

_Implementation mapping_: builtin assets live under `weft/builtins/tasks/`.
Explicit spec resolution lives in `weft/commands/specs.py` and
`weft/commands/run.py`.

## Related Plans

- [`docs/plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md`](../plans/2026-04-16-runtime-endpoint-registry-boundary-plan.md)

## Builtins

### `probe-agents`

Purpose:

- probe the known delegated provider CLIs that Weft has built-in adapters for
- write missing provider executable defaults into `.weft/agents.json`
- report what was found in ordinary task output

What it does:

- checks the current project context
- probes the registered delegated provider executables using the isolated probe
  helpers
- preserves existing explicit `.weft/agents.json` entries
- fills only missing provider executable entries
- reports probe results per provider, including version output when available

What it does not do:

- it does not run automatically at startup
- it does not prove provider login state as durable truth
- it does not overwrite explicit project settings by default
- it does not change bare command lookup

Why it fits:

- it is an explicit helper for discovering whether a delegated runtime path is
  available
- any settings mutation is limited to filling missing executable defaults while
  preserving existing explicit project settings
- it remains optional and is not part of the ordinary `weft run` path

Current output shape:

- `project_root`
- `settings_path`
- `summary`
- `providers`

Each provider report currently includes:

- provider name
- default executable
- configured executable, if any
- candidate executable string
- resolved executable path, if found
- settings action (`created`, `preserved`, or `unchanged`)
- probe status (`available`, `not_found`, or `probe_failed`)
- version output or probe error
- for `opencode`, explicit `run` support status

_Implementation mapping_: builtin TaskSpec asset:
`weft/builtins/tasks/probe-agents.json`. Runtime function target:
`weft/builtins/agent_probe.py` :: `probe_agents_task`. Probe helpers:
`weft/core/agents/provider_cli/probes.py`. Project settings mutation:
`weft/core/agents/provider_cli/settings.py`.

### `prepare-agent-images`

Purpose:

- warm the local Docker image cache for supported Docker-backed agent runtimes
- make the image-preparation step explicit and repeatable
- report which providers were reused, built, unsupported, or failed

What it does:

- requires the Docker runner extension to be installed
- current platform note: this builtin is supported on Linux and macOS, but not
  Windows
- accepts optional JSON input with `providers` and `refresh`
- stays plural because one run may warm multiple provider images
- when `providers` are supplied explicitly, it uses those names directly; no
  host CLI discovery is required
- defaults to probing known providers without mutating `.weft/agents.json`, then
  selecting only available providers
- calls the shared Docker image helper rather than owning any build logic
- current shipped image-recipe support is explicit; today that set is
  `claude_code`, `codex`, `gemini`, `opencode`, and `qwen`

What it does not do:

- it does not become a required startup step
- it does not mutate `.weft/agents.json` by default
- it does not imply that every host-discovered provider has a Docker image
  recipe
- it does not replace the real run path; real Docker-backed agent runs may
  still build on cache miss, but they reuse the same warmed deterministic
  image tags when those images already exist

Why it fits:

- it smooths a real runner path that Weft already owns rather than adding a new
  lifecycle surface
- it is an explicit latency optimization for restricted-runtime execution
- it remains optional and does not change correctness or ordinary run semantics

Current output shape:

- `project_root`
- `summary`
- `providers`

Each provider report currently includes:

- provider name
- recipe status (`supported` or `unsupported`)
- action (`built`, `reused`, `unsupported`, or `failed`)
- image tag and cache key when an image exists
- failure detail when the build or reuse path fails

_Implementation mapping_: builtin TaskSpec asset:
`weft/builtins/tasks/prepare-agent-images.json`. Runtime function target:
`weft/builtins/agent_images.py` :: `prepare_agent_images_task`. Shared Docker
image service: `extensions/weft_docker/weft_docker/agent_images.py`.

### `dockerized-agent`

Purpose:

- ship one explicit example of the Docker-backed one-shot delegated agent lane
- demonstrate Docker-specific late-bound file mounting from the agent work item
- provide a concrete example that reads a caller-supplied document and explains
  it in one sentence

What it does:

- uses `spec.type="agent"` with `spec.agent.runtime="provider_cli"`
- declares `spec.parameterization` so `weft run --spec dockerized-agent`
  accepts:
  - optional `--provider` with current choices `claude_code`, `codex`,
    `gemini`, `opencode`, and `qwen`
  - optional `--model`
- uses a bundle-local parameterization adapter to materialize a concrete
  provider-specific TaskSpec template before queueing
- declares `spec.run_input` so `weft run --spec dockerized-agent`
  accepts:
  - required `--prompt`
  - optional `--document`
  - optional piped stdin text
- uses a bundle-local run-input adapter to turn those later CLI inputs into the
  ordinary public agent work envelope before queueing
- selects the Docker runner through `spec.runner.name="docker"`
- current platform note: this builtin is supported on Linux and macOS, but not
  Windows
- uses an explicit runner environment profile to:
  - set Docker runner defaults for one-shot delegated execution
  - declare `spec.runner.options.work_item_mounts` so the raw work item field
    `metadata.document_path` is resolved to an absolute host file path and
    mounted read-only into the container at the shared example target path
    chosen by the helper module
  - leave Docker networking enabled so the delegated CLI can reach the provider
- uses a bundle-local delegated tool profile helper that applies provider-
  specific example defaults after materialization
- today that helper keeps the outer Docker boundary as the main isolation
  boundary and sets delegated `codex` options `sandbox="danger-full-access"`
  and `skip_git_repo_check=true`; other providers currently use their default
  one-shot Docker-backed invocation path
- defaults to `provider="codex"` when `--provider` is omitted
- if `--provider opencode` is selected and `--model` is omitted, the builtin
  materializes `spec.agent.model="openai/gpt-5"` so the example does not
  depend on whatever default model the local OpenCode install last remembered
- the builtin defaults to `authority_class="general"` so it stays
  representative of the broad delegated lane; if you want a read-only provider
  mode, copy the example and narrow authority explicitly in your own spec
- if `--provider claude_code` is selected, parameterization swaps in a
  Claude-specific runner environment profile for that materialized TaskSpec
- that Claude-specific profile injects `IS_SANDBOX=1` so Claude treats the
  Docker example as an explicit sandboxed runtime
- that Claude-specific profile preserves explicit portable auth env and an
  existing host `~/.claude/.credentials.json` when present
- on macOS, when `--provider claude_code` is selected and neither of those auth
  paths is present, the Claude-specific profile attempts a runtime-only
  Keychain lookup during real task startup and injects
  `CLAUDE_CODE_OAUTH_TOKEN` for that run only
- if that Keychain lookup fails, the Claude-specific example aborts at startup
  with an auth error instead of writing a Linux credential file
- piped stdin is the tighter path when you want the example to obey a strict
  output-shape prompt such as "exactly one sentence"; `--document` still
  proves late-bound Docker file mounts, but some providers may narrate their
  mounted-file read step before the final answer
- to point it at this repo's architecture overview, either:
  `cat /Users/van/Developer/weft/docs/specifications/00-Overview_and_Architecture.md | weft run --spec dockerized-agent --provider codex --prompt "Explain this document in one sentence."`
- or:
  `weft run --spec dockerized-agent --provider gemini --prompt "Explain this document in one sentence." --document /Users/van/Developer/weft/docs/specifications/00-Overview_and_Architecture.md`

What it does not do:

- it does not become a general Docker-agent launcher
- it does not hide auth or provider setup requirements
- it does not persist Claude credentials to a Linux `~/.claude/.credentials.json`
  file or write auth state back to the host
- it does not avoid the current public work-envelope requirement that agent
  tasks receive a string or JSON work item; its run-input adapter just builds
  that ordinary work item locally before queueing
- it does not teach the Docker runner or the agent runtime to choose providers
  from work-item metadata; provider selection happens only during local
  TaskSpec materialization before queueing

Why it fits:

- it remains an ordinary TaskSpec on the existing durable spine
- it teaches the Docker-backed one-shot agent shape through a concrete example
- runtime file selection stays explicit in the public work item rather than
  hidden in ordinary `weft run`

Why the shipped shape is a bundle:

- the `dockerized-agent` asset is a bundle because it demonstrates
  bundle-local Python refs for `spec.parameterization`,
  `spec.run_input`, `spec.runner.environment_profile_ref`, and the delegated
  tool profile hook
- the mounted-document prompt path is late-bound through template args from the
  shared helper module, so the example mount target is declared once in code
  rather than duplicated in both the mount helper and the prompt text

_Implementation mapping_: builtin TaskSpec asset:
`weft/builtins/tasks/dockerized-agent/taskspec.json`. Support code:
`weft/builtins/tasks/dockerized-agent/dockerized_agent.py` ::
`dockerized_agent_parameterization`,
`dockerized_agent_environment_profile`,
`dockerized_agent_run_input`,
`dockerized_agent_tool_profile`. Shared helper module:
`weft/builtins/dockerized_agent_examples.py`. Provider runtime descriptor:
`weft/core/agents/provider_cli/container_runtime.py`,
`weft/core/agents/provider_cli/runtime_descriptors/`.

## Related Plans

- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](../plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)
- [`docs/plans/2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md`](../plans/2026-04-14-builtin-contract-and-doc-drift-reduction-plan.md)
- [`docs/plans/2026-04-14-system-builtins-command-plan.md`](../plans/2026-04-14-system-builtins-command-plan.md)
- [`docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`](../plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md)
- [`docs/plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md`](../plans/2026-04-14-docker-agent-images-and-one-shot-provider-cli-plan.md)
- [`docs/plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md`](../plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md)
- [`docs/plans/2026-04-14-weft-road-to-excellent-plan.md`](../plans/2026-04-14-weft-road-to-excellent-plan.md)
- [`docs/plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`](../plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md)
- [`docs/plans/2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md`](../plans/2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md)
- [`docs/plans/2026-04-15-parameterized-taskspec-materialization-plan.md`](../plans/2026-04-15-parameterized-taskspec-materialization-plan.md)
- [`docs/plans/2026-04-15-spec-aware-run-help-plan.md`](../plans/2026-04-15-spec-aware-run-help-plan.md)
- [`docs/plans/2026-04-16-docker-builtins-windows-guard-plan.md`](../plans/2026-04-16-docker-builtins-windows-guard-plan.md)

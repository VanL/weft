# Docker Container Profiles Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3], [CC-3.1]; docs/specifications/02-TaskSpec.md [TS-1.3]; docs/specifications/13-Agent_Runtime.md [AR-0.0], [AR-0.1]
Superseded by: none

## 1. Goal

Add a portable `weft_docker` feature that lets a host-managed Weft manager run
selected command tasks inside a Docker execution locality when task dependencies
only resolve from inside a Docker network. The feature should make the existing
project-local Python `environment_profile_ref` pattern declarative for common
Docker command tasks without moving project topology, Compose policy, or
service-specific configuration into Weft core.

The motivating shape is:

- queue operations succeed from both host and container because the broker is
  reachable, often through Postgres
- service names such as `db`, `redis`, `wazuh.indexer`, or `internal-api` may
  resolve only inside a Docker network
- the durable manager should remain on the host for systemd supervision and
  operator control
- only the task's execution process should cross into a Docker context

## 2. Source Documents

- `docs/specifications/01-Core_Components.md [CC-3], [CC-3.1]`: `TaskRunner`
  dispatches through runner plugins; core owns lifecycle, queues, and control;
  plugins own runtime-specific execution details.
- `docs/specifications/02-TaskSpec.md [TS-1.3]`: `spec.runner.name`,
  `spec.runner.options`, and `spec.runner.environment_profile_ref` are the
  runner selection and runner-default materialization surfaces. Stored TaskSpecs
  must remain JSON-serializable and portable. Runtime availability checks belong
  to explicit validation or preflight, not ordinary submission.
- `docs/specifications/13-Agent_Runtime.md [AR-0.0], [AR-0.1]`: Docker-backed
  execution is runner-specific. Core owns the durable task spine; Docker owns
  container execution.
- `docs/agent-context/engineering-principles.md`: keep runner features on the
  existing `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`
  spine.
- `docs/agent-context/runbooks/testing-patterns.md`: use real production paths
  when practical, avoid mock-heavy queue/process substitutes, and keep external
  boundaries mocked narrowly.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: this plan changes runner
  behavior, so it must name invariants, hidden couplings, tests, rollback, and
  review gates.

Existing project evidence, useful context only:

- `/Users/van/Developer/mm-governance/.weft/tasks/wazuh-indexer-probe.json`
- `/Users/van/Developer/mm-governance/agents/weft_profiles.py`
- `/Users/van/Developer/mm-governance/memory/2026-05-26-weft-wazuh-indexer-dns.md`

Those files demonstrate the use case. Do not copy Wazuh, mm-governance, or
Compose-specific values into Weft.

## 3. Decision Summary

Implement **Docker container profiles** inside `extensions/weft_docker`, exposed
through runner-specific `spec.runner.options`. A TaskSpec keeps the normal
runner contract:

```json
{
  "spec": {
    "type": "command",
    "process_target": "/opt/venv/bin/python",
    "args": ["-m", "my_project.probes.internal_service"],
    "runner": {
      "name": "docker",
      "options": {
        "container_profile": "ops"
      }
    }
  }
}
```

The profile is project-local TOML:

```toml
version = 1

# Optional. Relative values are resolved from this file's directory.
# Use this when the profile file lives under .weft/ but mounts should resolve
# from the project root.
root = ".."

[profiles.ops]
image = "ghcr.io/example/project:latest"
network = "project_ops"
mount_workdir = false
container_workdir = "/app/project"
env_from_host = ["OPTIONAL_TOKEN"]
required_env_from_host = ["REQUIRED_TOKEN"]

[profiles.ops.env]
SERVICE_URL = "https://internal-service:8443"
POSTGRES_HOST = "db"

[[profiles.ops.mounts]]
source = ".env"
target = "/app/project/.env"
read_only = true

[[profiles.ops.mounts]]
source = "config"
target = "/app/project/config"
read_only = true
```

Default profile lookup:

- if `spec.runner.options.container_profile` is absent, existing Docker runner
  behavior is unchanged
- if it is present, the Docker runner reads
  `spec.runner.options.container_profile_file` or defaults to
  `.weft/docker-profiles.toml`
- relative profile-file paths resolve against the TaskSpec bundle root when one
  exists, otherwise against the current process working directory
- profile-relative mount and build paths resolve against
  `spec.runner.options.container_profile_root`, then TOML top-level `root`,
  then the profile file's directory
- `root` and `container_profile_root` are path bases only. They do not change
  the Weft project context or broker target.

Merge precedence:

1. profile-defined Docker options and env are defaults
2. `env_from_host` and `required_env_from_host` copy explicitly named host
   environment variables into profile env
3. explicit TaskSpec `spec.env` wins over profile env and forwarded host env
4. explicit TaskSpec `spec.runner.options` wins over profile Docker options
5. after merge, existing Docker runner validation still owns conflicts such as
   both `image` and `build` being present

`required_env_from_host` means the merged task env must contain that key after
profile env, host forwarding, and explicit `spec.env` are considered. An
explicit `spec.env` value satisfies the requirement. Missing host env should
not fail when the TaskSpec supplied the same key explicitly.

This is deliberately not called a core "execution context". That name is too
broad and would invite policy into Weft core. The feature is a Docker runner
container profile.

## 4. Current Context and Key Files

Files to modify for implementation:

- `extensions/weft_docker/weft_docker/profiles.py`: new helper module for
  loading, validating, and materializing container profiles.
- `extensions/weft_docker/weft_docker/plugin.py`: wire profile materialization
  into command TaskSpec validation and runner creation. Keep Docker command
  construction here.
- `extensions/weft_docker/weft_docker/__init__.py`: export only if a public
  helper is intentionally exposed. Do not export private parser internals.
- `extensions/weft_docker/README.md`: document the feature and examples.
- `docs/specifications/01-Core_Components.md`: add a plan backlink and update
  implementation notes if the runner boundary text changes.
- `docs/specifications/02-TaskSpec.md`: document that `container_profile` is a
  Docker runner option, not TaskSpec schema.
- `docs/specifications/13-Agent_Runtime.md`: update Docker-backed runner notes
  only if implementation affects the Docker command lane text.
- `docs/plans/README.md`: add this plan row.

Files to read before editing:

- `weft/core/tasks/consumer.py`: `Consumer._make_task_runner()` passes runner
  name, options, env, working dir, and bundle root into `TaskRunner`.
- `weft/core/tasks/runner.py`: `TaskRunner` materializes environment profiles,
  validates runner capabilities, loads the runner plugin, and delegates
  execution. Do not bypass it.
- `weft/core/environment_profiles.py`: existing Python profile materialization
  semantics and explicit-value precedence.
- `weft/core/runner_validation.py`: explicit preflight and plugin validation
  layering.
- `extensions/weft_docker/weft_docker/plugin.py`: current Docker command runner,
  Docker validation, build option validation, mount normalization, runtime
  handle publication, and stop/kill implementation.
- `extensions/weft_docker/weft_docker/agent_runner.py`: one-shot provider CLI
  Docker lane. Read to avoid accidentally changing agent-task behavior.
- `tests/tasks/test_command_runner_parity.py`: cross-runner command behavior
  tests and existing pure Docker command-construction tests.
- `extensions/weft_docker/tests/test_docker_plugin.py`: Docker plugin validation
  and runtime-handle tests.
- `tests/core/test_environment_profiles.py`: current environment profile merge
  rules.
- `tests/cli/test_cli_validate.py`: current validation/preflight output, useful
  only if implementation touches CLI validation text.

Comprehension questions before editing:

1. Which function in core passes `spec.runner.options` and
   `environment_profile_ref` into `TaskRunner`, and why must this feature not
   bypass that path?
2. Where does `weft_docker` currently turn Docker runner options into
   `docker run` arguments?
3. What does `validate_taskspec_runner(..., preflight=True)` do that ordinary
   task execution does not do?
4. Why is `docker exec` into an existing service container a different
   lifecycle model from Weft's current fresh-container Docker runner?

## 5. Invariants and Constraints

Preserve these invariants:

- No new durable execution path. Work must still flow through
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> Docker runner ->
  queues/state log`.
- No TaskSpec schema change in this slice. `container_profile` is a
  `spec.runner.options` key owned by `weft_docker`.
- `spec` and `io` remain immutable after resolved TaskSpec creation. Do not
  mutate the `TaskSpec` object to apply profiles.
- TID, queue names, reserved queue policy, outbox behavior, timeout behavior,
  stop/kill behavior, and runtime-handle shape must not change.
- Do not add profile-specific hidden probes to normal `weft run` or ordinary
  task startup. New image, network, mount-source, and profile-file checks belong
  to explicit preflight validation. If existing Docker-runner daemon checks
  during ordinary validation need to move, that is a separate compatibility
  plan rather than part of this slice.
- Existing Python `environment_profile_ref` must continue to work. This feature
  reduces the need for project-local Python glue; it does not remove or replace
  the existing hook.
- Existing explicit Docker runner options must keep their current meaning.
  Profile values are defaults and must not override explicit TaskSpec options.
- Profile files must be data-only TOML. Do not add Python evaluation, shell
  interpolation, command substitution, or automatic `.env` parsing.
- No Wazuh, mm-governance, Compose service, or Postgres-specific behavior may
  be built into Weft or `weft_docker`.
- No automatic Compose network discovery in this slice. A profile may name a
  Docker network; the runner should not parse `docker-compose.yml`.
- No `docker compose exec` or `docker exec` into long-running containers. Keep
  the current one-shot `docker run` lifecycle so Weft can own timeout, stop,
  kill, stdout, stderr, and cleanup.
- Do not add a dependency. Use the standard library `tomllib` for TOML parsing.

Hidden coupling to call out:

- The host manager may run with a host-safe broker endpoint while task
  containers need container-safe service names. This feature must affect only
  the execution environment of the task container, not manager context
  resolution or broker configuration.
- Stored TaskSpecs are often loaded from `.weft/tasks/*.json`, which are not
  bundle directories. If a profile file path is relative and no bundle root is
  present, the Docker runner can only resolve it relative to the current process
  working directory. Document this clearly.
- Direct construction of `DockerCommandRunner` in tests bypasses
  `DockerRunnerPlugin.create_runner()`. Profile materialization should be owned
  by the plugin boundary; direct `DockerCommandRunner` tests should pass already
  materialized options.

Review gates:

- Stop and re-plan if the implementation needs to change manager startup,
  broker context resolution, SimpleBroker, queue schemas, or public task result
  payloads.
- Stop and re-plan if the feature starts looking like a cross-runner
  abstraction. The requested feature is Docker-specific.
- Stop and re-plan if supporting project root discovery requires new core
  TaskSpec fields. Use explicit profile paths and bundle-root/current-working
  directory resolution for this slice.
- Stop and re-plan if tests need to mock the manager, queues, and task state
  just to prove the feature. That is a signal the implementation crossed the
  wrong boundary.

## 6. Rollback and Rollout

Rollback is simple if this remains a runner-options feature:

- Existing TaskSpecs without `spec.runner.options.container_profile` continue
  to behave as before.
- A project can roll back by replacing `container_profile` with explicit Docker
  runner options or by returning to a project-local `environment_profile_ref`.
- No queue data, TaskSpec persisted format, or runtime handle needs migration.
- No destructive cleanup or one-way storage change is introduced.

Rollout sequence:

1. Land parser and merge helper with unit tests.
2. Wire helper into Docker command validation and runner creation.
3. Add explicit preflight checks for profile file, required forwarded env,
   Docker binary, Docker daemon, named network, image or build configuration,
   and profile-sourced mount/build source paths.
4. Update docs and specs after the behavior is implemented and verified.
5. Convert one external project, such as mm-governance, only after this lands
   and is released. That conversion is out of scope for this Weft plan.

Post-deploy or runtime observation for adopters:

- `weft spec validate --type task PATH --load-runner` should prove profile
  parsing and plugin availability without requiring service reachability.
- `weft spec validate --type task PATH --preflight` should prove Docker daemon,
  network, image/build, required env, and profile-sourced mounts where
  practical.
- A real `weft run --spec PATH --no-wait` should leave normal task-log,
  outbox, and runtime-handle evidence. The runner should report a Docker
  startup failure through the existing task failure path if the container
  cannot start.

## 7. Bite-Sized Tasks

1. Add failing tests for profile parsing and merge semantics.
   - Outcome: lock the intended data contract before writing implementation.
   - Files to touch:
     - `extensions/weft_docker/tests/test_container_profiles.py` (new)
   - Read first:
     - `tests/core/test_environment_profiles.py`
     - `extensions/weft_docker/weft_docker/plugin.py` mount and build option
       normalization helpers
   - Tests to write first:
     - `container_profile="ops"` loads `.weft/docker-profiles.toml` and
       returns Docker options plus env.
     - `container_profile_file` supports an explicit relative file path.
     - `root = ".."` resolves profile-sourced mount paths relative to the
       selected root.
     - explicit TaskSpec runner options override profile options.
     - explicit TaskSpec env overrides profile env and forwarded host env.
     - `required_env_from_host` fails when a named variable is missing from both
       host env and explicit TaskSpec env.
     - unknown profile name fails with a message that names the profile and
       profile file.
   - Do not mock:
     - TOML parsing
     - path normalization
   - Mock only:
     - process environment through `monkeypatch.setenv/delenv`
   - Stop if:
     - the tests require importing core manager or queue code. Profile parsing
       belongs in `weft_docker`.
   - Done when:
     - the new tests fail for the expected missing implementation, not because
       of unrelated import or fixture errors.

2. Implement `weft_docker.profiles`.
   - Outcome: one small, tested helper owns profile-file loading and merge
     semantics.
   - Files to touch:
     - `extensions/weft_docker/weft_docker/profiles.py` (new)
   - Required implementation shape:
     - use `from __future__ import annotations`
     - stdlib imports only, especially `tomllib`, `dataclasses`, `pathlib`,
       `collections.abc`, and `typing.Any` where needed
     - use dataclasses with `frozen=True, slots=True` for parsed profile
       results if a structured return type helps readability
     - expose one narrow helper, for example
       `materialize_container_profile(...)`
   - Suggested helper signature:
     ```python
     def materialize_container_profile(
         *,
         runner_options: Mapping[str, Any],
         env: Mapping[str, str] | None,
         working_dir: str | None,
         bundle_root: str | None,
         preflight: bool = False,
     ) -> MaterializedContainerProfile:
         ...
     ```
   - Return fields should include:
     - merged `runner_options`
     - merged `env`
     - `working_dir`
     - optional metadata for diagnostics
   - Required schema:
     - top-level `version = 1` is optional for now but, if present, must be
       integer `1`
     - top-level `root` is optional and must be a non-empty string when present
     - top-level `profiles` must be a table when `container_profile` is used
     - `profiles.<name>.env` must be a string-to-string mapping
     - `profiles.<name>.env_from_host` and
       `profiles.<name>.required_env_from_host` live at the profile table level,
       not under `profiles.<name>.env`, and must be lists of non-empty strings
     - supported Docker option keys in a profile are the existing command-runner
       keys: `image`, `build`, `docker_binary`, `docker_args`,
       `container_workdir`, `mount_workdir`, `network`, and `mounts`
   - Path rules:
     - relative `container_profile_file` resolves against `bundle_root` when
       present, otherwise `Path.cwd()`
     - relative TOML `root` resolves against the profile file's parent
     - relative `container_profile_root` resolves against `bundle_root` when
       present, otherwise `Path.cwd()`
     - profile-sourced `mounts[].source`, `build.context`, and
       `build.dockerfile` resolve against the selected profile root
     - explicit TaskSpec runner options are not rewritten by the profile helper
       unless they are the profile control keys
   - Control keys to remove before Docker command construction:
     - `container_profile`
     - `container_profile_file`
     - `container_profile_root`
   - Reuse:
     - mirror error-message style from `plugin.py` validators
   - Do not add:
     - env interpolation
     - `.env` parsing
     - Compose parsing
     - Docker SDK calls in the parser
   - Done when:
     - tests from task 1 pass.

3. Wire container profiles into Docker command validation and runner creation.
   - Outcome: TaskSpecs can use `runner.options.container_profile` through the
     normal Docker runner plugin path.
   - Files to touch:
     - `extensions/weft_docker/weft_docker/plugin.py`
     - `extensions/weft_docker/tests/test_docker_plugin.py`
   - Read first:
     - `weft/core/runner_validation.py`
     - `weft/core/tasks/runner.py`
     - `extensions/weft_docker/weft_docker/plugin.py`
   - Implementation details:
     - call `materialize_container_profile(...)` inside
       `DockerRunnerPlugin.validate_taskspec()` before checking command runner
       options
     - pass the materialized payload to existing command validation helpers
       rather than duplicating image/build/mount/network validation
     - call the same helper inside `DockerRunnerPlugin.create_runner()` before
       constructing `DockerCommandRunner`
     - pass `bundle_root` into `DockerCommandRunner` only if the runner itself
       still needs it after plugin materialization. Prefer doing profile
       resolution at the plugin boundary.
     - reject `container_profile` for agent tasks in this slice with a clear
       `ValueError`, unless the implementation can support agent tasks without
       changing provider image semantics. The conservative default is reject.
   - Tests:
     - plugin validation accepts a command TaskSpec with `container_profile`
       after materialization provides an image
     - plugin validation rejects `container_profile` on `spec.type="agent"` with
       a clear message
     - `create_runner()` materializes env and runner options before building
       `DockerCommandRunner`
     - profile control keys do not appear in the materialized Docker runner
       options or final Docker command state
   - What not to mock:
     - plugin validation function under test
     - profile helper
   - What to mock:
     - Docker client ping, Docker binary lookup, and Docker SDK calls, because
       Docker availability is an external boundary
   - Stop if:
     - implementation wants to change `TaskRunner` or `Consumer` to know about
       Docker profiles. That would put a Docker-specific feature in core.
   - Done when:
     - the new plugin tests pass and existing Docker plugin tests still pass.

4. Make container workdir semantics explicit for command tasks.
   - Outcome: a profile can set a container working directory without requiring
     the host working directory to be mounted.
   - Files to touch:
     - `extensions/weft_docker/weft_docker/plugin.py`
     - `extensions/weft_docker/tests/test_docker_plugin.py`
     - `tests/tasks/test_command_runner_parity.py` if the shared command
       runner parity test is the cleaner location
   - Current behavior:
     - `_build_docker_command()` adds `--workdir` only when
       `mount_workdir=True` and `working_dir` is set.
     - `container_workdir` has no effect when `mount_workdir=False`.
   - Required behavior:
     - if `container_workdir` is set, include `--workdir <container_workdir>`
       even when `mount_workdir=False`
     - if `mount_workdir=True` and `working_dir` is set, keep the existing host
       workdir bind mount and use `container_workdir` as the mount target when
       supplied
     - avoid duplicate `--workdir` entries
   - Tests:
     - profile with `mount_workdir=false` and `container_workdir="/app"` builds
       a Docker command containing `--workdir /app`
     - default behavior with no `container_workdir` remains unchanged
     - `mount_workdir=true` with host `working_dir` still mounts and sets
       workdir as before
   - Stop if:
     - this change breaks an existing documented behavior. If a test shows that
       users depended on `container_workdir` being ignored, split this into a
       separate compatibility plan.
   - Done when:
     - command-construction tests prove no duplicate `--workdir` and existing
       command runner parity tests pass.

5. Add explicit preflight checks for profile-backed command tasks.
   - Outcome: `--preflight` catches common Docker-context mistakes without
     ordinary `weft run` doing hidden probes.
   - Files to touch:
     - `extensions/weft_docker/weft_docker/plugin.py`
     - `extensions/weft_docker/weft_docker/profiles.py`
     - `extensions/weft_docker/tests/test_docker_plugin.py`
   - Required checks when `preflight=True`:
     - profile file exists and parses
     - selected profile exists
     - required forwarded environment variables are present
     - Docker binary resolves
     - Docker daemon responds
     - named `network` exists, if a network is configured
     - profile-sourced mount sources exist on the host
     - profile-sourced build context and Dockerfile exist when `build` is used
   - Do not add these checks to ordinary `preflight=False` execution. The real
     run should attempt the Docker command and report startup failure through
     the normal task outcome path.
   - Tests:
     - missing profile file fails at profile layer
     - missing required env fails before Docker calls
     - missing network fails only during preflight
     - `preflight=False` with a configured network does not call network
       inspection
   - Mock only:
     - Docker SDK client and Docker binary lookup
   - Done when:
     - preflight tests prove positive and negative cases without requiring a
       real local Docker daemon.

6. Add one production-path regression at the runner boundary.
   - Outcome: prove that `TaskRunner` can drive a Docker command TaskSpec with
     a container profile without core knowing about the profile.
   - Files to touch:
     - `tests/tasks/test_runner.py` or
       `tests/tasks/test_command_runner_parity.py`
   - Approach:
     - build a `TaskRunner` with `runner_name="docker"` and
       `runner_options={"container_profile": "ops", ...}`
     - provide a temporary profile file
     - monkeypatch only the Docker plugin's external Docker calls
     - assert the Docker backend receives materialized image, network, mounts,
       env, and container workdir
   - Do not:
     - mock `TaskRunner`
     - mock `materialize_runner_environment`
     - mock `require_runner_plugin`
   - Stop if:
     - this test becomes a full manager/queue lifecycle test. The feature is
       runner-local; existing tests already cover manager-to-consumer dispatch.
   - Done when:
     - the test proves core passes runner options through unchanged and the
       Docker plugin owns interpretation.

7. Update docs and specs.
   - Outcome: the feature has a clear path for users and traceability back to
     specs.
   - Files to touch:
     - `extensions/weft_docker/README.md`
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/13-Agent_Runtime.md` if command-lane wording needs
       adjustment
     - `docs/plans/README.md`
   - README content:
     - host-managed manager, Docker-local command task example
     - minimal TaskSpec example
     - `.weft/docker-profiles.toml` example
     - path resolution rules
     - env precedence and required forwarded env behavior
     - `weft spec validate --preflight` guidance
     - explicit statement that this does not use `docker exec` or parse Compose
   - Spec updates:
     - `02-TaskSpec.md [TS-1.3]`: mention that Docker container profiles are
       runner-specific `runner.options`, not TaskSpec schema.
     - `01-Core_Components.md [CC-3.1]`: keep the runner plugin boundary clear.
     - `13-Agent_Runtime.md`: update only if command-lane Docker text needs a
       backlink or a note that profiles currently target command tasks.
   - Done when:
     - docs include owner, boundary, verification, and required action for a
       user trying to run a host-managed manager with Docker-local tasks.

8. Run final verification gates.
   - Outcome: implementation is proven without relying on a local project's
     Wazuh stack or Docker Compose topology.
   - Commands:
     ```bash
     . ./.envrc
     ./.venv/bin/python -m pytest extensions/weft_docker/tests -q
     ./.venv/bin/python -m pytest tests/tasks/test_command_runner_parity.py -q
     ./.venv/bin/python -m pytest tests/tasks/test_runner.py -q
     ./.venv/bin/python -m pytest tests/core/test_environment_profiles.py -q
     ./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
     ./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
     ./.venv/bin/ruff check weft extensions/weft_docker
     ```
   - If Docker is unavailable locally:
     - tests must still pass because external Docker calls are mocked in unit
       tests
     - any optional manual smoke that requires Docker should be reported as not
       run
   - Manual smoke, optional when Docker is available:
     - create a temporary profile using a small local image such as busybox or a
       known test image already used by the repo
     - run `weft spec validate --type task PATH --preflight`
     - run a no-wait command task and then `weft result TID`
   - Stop if:
     - final verification requires a real mm-governance, Wazuh, or Compose
       stack. That would prove the wrong thing.

## 8. Testing Plan

Use red-green TDD for every behavior that can be described without a real
Docker daemon:

- profile file parsing and merge: start in
  `extensions/weft_docker/tests/test_container_profiles.py`
- plugin validation and preflight: start in
  `extensions/weft_docker/tests/test_docker_plugin.py`
- Docker command construction: extend existing pure command-construction tests
  rather than launching Docker
- runner boundary: add one `TaskRunner`-level test with only Docker's external
  process/client calls mocked

Observable behavior to assert:

- final Docker command contains the expected `--network`, `--volume`, `--env`,
  `--workdir`, image, and inner command
- merged env contains profile env, forwarded host env, and explicit TaskSpec
  env with the right precedence
- validation errors name the profile, file, and key that failed
- preflight-only checks do not run during ordinary validation/execution
- existing no-profile Docker runner tests continue to pass

What not to mock:

- profile parser
- merge helper
- `DockerRunnerPlugin.validate_taskspec()`
- `DockerRunnerPlugin.create_runner()`
- `TaskRunner` in the one runner-boundary regression
- queues, manager, or task lifecycle if a test claims to prove lifecycle

What may be mocked:

- Docker daemon/client calls
- Docker binary lookup
- subprocess launch in command-construction or runtime-start tests
- process environment with `monkeypatch`

Do not add a full manager/Consumer integration test for this slice unless a
bug appears at that boundary. The change is intended to be runner-local. A
manager-heavy test would be slower and would likely obscure the contract under
test.

## 9. Out of Scope

- Core TaskSpec schema changes.
- A generic cross-runner "execution context" abstraction.
- Compose parsing, Compose service discovery, `docker compose exec`, or
  `docker exec`.
- Running tasks inside an existing long-running service container.
- Automatic service health probes or hidden startup checks during ordinary
  `weft run`.
- Project-specific profiles for mm-governance, Wazuh, Grafana, Redis, Django,
  or any other product.
- Secret interpolation, `.env` parsing, shell expansion, or command
  substitution inside profile files.
- Agent-task support for container profiles. This can be planned later if a
  real provider-cli use case appears.
- Windows support. The Docker runner is already unsupported on Windows.

## 10. Independent Review Loop

This is a reusable runner contract, so it needs review before implementation
lands.

Reviewer prompt:

> Read `docs/plans/2026-05-28-docker-container-profiles-plan.md`, then inspect
> `docs/specifications/01-Core_Components.md [CC-3]`,
> `docs/specifications/02-TaskSpec.md [TS-1.3]`,
> `extensions/weft_docker/weft_docker/plugin.py`,
> `weft/core/tasks/runner.py`, and the Docker runner tests. Look for errors,
> bad ideas, and latent ambiguities. Do not implement. Answer whether a
> zero-context engineer could implement this confidently and correctly.

The implementer must respond to each review point explicitly before coding. If
the reviewer says this should become a core Weft abstraction, the implementer
must stop and re-evaluate against the user goal and the constraints in this
plan. Do not quietly drift from a Docker-runner feature into a core execution
context redesign.

## 11. Fresh-Eyes Self-Review

Self-review status: completed during plan authoring.

Findings fixed before this draft:

- Ambiguous term: the first draft direction used "execution context", which is
  too broad and could push the implementation into core Weft. The plan now uses
  "Docker container profile" and keeps ownership in `weft_docker`.
- Missing root rule: a profile file under `.weft/` makes relative mount paths
  ambiguous. The plan now defines TOML `root`, `container_profile_root`, and
  path resolution precedence.
- Secret handling gap: a data-only profile still needs a way to pass selected
  host env into the container. The plan now includes explicit
  `env_from_host` and `required_env_from_host`, with no interpolation.
- Workdir gap: current Docker runner behavior does not apply
  `container_workdir` unless host workdir mounting is active. The plan now
  makes explicit container workdir behavior its own testable task.
- Test risk: a naive implementer might over-mock the plugin and only test the
  parser. The plan now requires plugin and `TaskRunner` boundary tests while
  mocking only Docker's external client/process boundary.
- TOML placement bug: `env_from_host` and `required_env_from_host` must live on
  the profile table, not inside `profiles.<name>.env`. The example and schema
  text now make that explicit.
- Scope creep risk: broad "no hidden Docker probes" wording could pull this
  slice into changing existing Docker-runner validation behavior. The invariant
  now forbids new profile-specific probes during ordinary runs and leaves any
  existing Docker validation cleanup to a separate compatibility plan.

Residual risk:

- Relative profile-file resolution still depends on bundle root or current
  process working directory. That is acceptable for this slice because adding
  a core project-root field would broaden the feature. If real adoption shows
  this is too brittle, write a separate core-boundary plan.
- The plan intentionally rejects agent-task profile support. That is a
  conservative YAGNI boundary, but it may need a follow-up once command-task
  profiles are proven.

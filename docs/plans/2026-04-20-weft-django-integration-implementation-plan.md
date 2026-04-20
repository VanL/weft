# Weft Django Integration Implementation Plan

Status: completed
Source specs: see Source Documents below
Superseded by: ./2026-04-20-weft-django-v1-reality-alignment-plan.md

## Goal

Implement the first-party `weft-django` package described by
`docs/specifications/13C-Using_Weft_With_Django.md`, keep it in this repo as a
separately installable PyPI package under `integrations/weft_django/`, and add
package-specific GitHub Actions release gates so a pushed `weft_django/vX.Y.Z`
tag tests the integration package and publishes it through the existing shared
PyPI workflow, the same way `weft-docker` and `weft-macos-sandbox` work today.

## Source Documents

Source specs:
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-0.1], [DJ-0.2],
  [DJ-2.1], [DJ-2.2], [DJ-3.1], [DJ-5.1], [DJ-6.1], [DJ-6.3], [DJ-7.1],
  [DJ-8.1], [DJ-8.2], [DJ-8.4], [DJ-9.1], [DJ-10.2], [DJ-10.3], [DJ-11.1],
  [DJ-12.1], [DJ-13.1], [DJ-13.2], [DJ-13.3], [DJ-13.4], [DJ-14.2],
  [DJ-17.1], [DJ-17.2], [DJ-17.3]
- `docs/specifications/13-Agent_Runtime.md` [AR-0], [AR-2]
- `docs/specifications/02-TaskSpec.md` [TS-1], [TS-1.3]
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]
- `docs/specifications/08-Testing_Strategy.md` [TS-0]
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]

Source guidance:
- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Relevant existing plans and behavior:
- `docs/plans/2026-04-06-release-gated-tag-workflow-plan.md`
- `.github/workflows/release.yml`
- `.github/workflows/release-gate-docker.yml`
- `.github/workflows/release-gate-macos-sandbox.yml`
- `.github/workflows/test.yml`
- `bin/release.py`
- `tests/system/test_release_script.py`

## Context and Key Files

Files to modify:
- `weft/client.py` (new)
- `weft/__init__.py`
- `pyproject.toml`
- `README.md`
- `bin/release.py`
- `.github/workflows/test.yml`
- `.github/workflows/release-gate-django.yml` (new)
- `integrations/weft_django/pyproject.toml` (new)
- `integrations/weft_django/README.md` (new)
- `integrations/weft_django/weft_django/__init__.py` (new)
- `integrations/weft_django/weft_django/apps.py` (new)
- `integrations/weft_django/weft_django/conf.py` (new)
- `integrations/weft_django/weft_django/registry.py` (new)
- `integrations/weft_django/weft_django/decorators.py` (new)
- `integrations/weft_django/weft_django/client.py` (new)
- `integrations/weft_django/weft_django/worker.py` (new)
- `integrations/weft_django/weft_django/urls.py` (new)
- `integrations/weft_django/weft_django/views.py` (new)
- `integrations/weft_django/weft_django/sse.py` (new)
- `integrations/weft_django/weft_django/channels.py` (new, optional extra path)
- `integrations/weft_django/weft_django/management/commands/*.py` (new)
- `integrations/weft_django/tests/...` (new integration test tree)
- `tests/core/...` or `tests/commands/...` for the shared `weft.client` API
- `tests/system/test_release_script.py`
- `docs/specifications/13C-Using_Weft_With_Django.md`
- `docs/specifications/08-Testing_Strategy.md`
- `docs/plans/README.md`

Files to read first:
- `docs/specifications/13C-Using_Weft_With_Django.md`
- `docs/specifications/13-Agent_Runtime.md`
- `docs/specifications/02-TaskSpec.md`
- `weft/commands/run.py`
- `weft/commands/_result_wait.py`
- `weft/context.py`
- `weft/core/spawn_requests.py`
- `weft/core/spec_store.py`
- `extensions/weft_docker/pyproject.toml`
- `extensions/weft_macos_sandbox/pyproject.toml`
- `.github/workflows/release.yml`
- `.github/workflows/release-gate-docker.yml`
- `.github/workflows/release-gate-macos-sandbox.yml`
- `.github/workflows/test.yml`
- `bin/release.py`
- `tests/system/test_release_script.py`

Shared paths and helpers to reuse:
- `build_context()` remains the only context-resolution entry point
- `submit_spawn_request()` and the existing result-wait helpers remain the
  canonical durable submission / wait path; `weft.client` should wrap them, not
  replace them
- `resolve_spec_reference()` remains the only named spec / bundle / path
  resolution path
- `.github/workflows/release.yml` remains the only package build / publish /
  trusted-publishing workflow
- the extension release gates and `bin/release.py` target model remain the
  template for package-specific tag publishing
- broker-backed tests should reuse real Weft harnesses and real Django
  transactions rather than mock-only stand-ins

Current structure:
- Weft has no stable public Python submission API today; the useful pieces live
  in CLI helpers, `weft/context.py`, `weft/core/spawn_requests.py`, and
  `weft/commands/_result_wait.py`
- first-party optional packages currently live in-repo under `extensions/` and
  publish from their own `pyproject.toml` via namespaced tags
- `bin/release.py` already knows about package-specific release targets and
  namespaced tags for `weft-docker` and `weft-macos-sandbox`
- `13C` intentionally keeps `weft_django` thin: Django is a submission and
  inspection layer, not a new Weft runner or task-truth store

Comprehension questions before editing:
- Which path should own actual spawn submission for Django callers? Answer:
  the new `weft.client` wrapper over existing context / spawn / wait helpers,
  not `weft.commands.run` and not a second execution path.
- Which package should own task lifecycle truth? Answer: Weft queues and task
  logs, not Django models.
- Which workflow should publish `weft-django`? Answer:
  `.github/workflows/release.yml`, called from a package-specific
  `release-gate-django.yml`.

## Invariants and Constraints

- Keep the current durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> runner plugin -> queues/state log`.
- Do not create a second submission or wait path for Python callers. The new
  `weft.client` API must wrap existing core behavior.
- `weft_django` must not introduce a `django` runner or rewrite native Weft
  runner choice. Decorated Django callables default to `host`; native Weft
  TaskSpecs and pipelines keep their declared runner.
- `weft_django` must not add Django models as task-lifecycle truth.
- HTTP surfaces remain read-only diagnostics in v1. No stop/kill HTTP
  endpoints.
- `DJANGO_SETTINGS_MODULE` must not be serialized into TaskSpec env by default.
- `enqueue_on_commit(..., wait=True)` and the matching native-spec deferred
  helpers with `wait=True` must fail locally.
- Keep the package separately installable from PyPI with its own
  `pyproject.toml`, version, README, and release tag namespace
  `weft_django/vX.Y.Z`.
- Do not create a second publish workflow. Reuse `.github/workflows/release.yml`
  exactly as the build / publish owner.
- Keep the main repo install working. Root `pyproject.toml` may grow a Django
  extra and local editable source, but no core runtime path should depend on
  Django being installed.
- Core package must not gain a hard dependency on Django.
- Testing must keep real broker / transaction / cross-process proofs where the
  contract depends on them. Do not mock away `transaction.on_commit()`, manager
  startup, or result visibility.

Rollout and rollback notes:
- Ship the stable `weft.client` slice before the Django package depends on it.
- Ship the integration package and its tests before enabling package-specific
  tag publishing.
- Add the release helper target and release-gate workflow only after the
  package metadata and package tests exist.
- Rollback stays straightforward if each slice is additive:
  the new package can be removed without changing core task behavior, and the
  release gate can be deleted without touching the shared publish workflow.

Review gates:
- Independent review is required after the `weft.client` slice and again after
  the `weft_django` package slice, before CI/release changes land.
- Prefer a different agent family than the author if one is available.
- This planning pass did not run that independent review; treat it as pending
  before implementation starts.

Out of scope:
- Django-side domain models, report rows, delivery rows, or other application
  artifacts
- public control-plane semantics for persistent operator-facing sessions
- Celery-compatible retry / ETA / beat behavior
- HTTP control endpoints
- a generic async-callable contract in core Weft

## Tasks

1. Add the stable core Python client API in `weft`.
   - Outcome: Weft exposes a supported Python submission / status / result API
     that Django and other embedding layers can call without importing CLI
     command modules.
   - Files to touch:
     - `weft/client.py` (new)
     - `weft/__init__.py`
     - targeted tests under `tests/core/` or `tests/commands/`
   - Read first:
     - `weft/commands/run.py`
     - `weft/commands/_result_wait.py`
     - `weft/context.py`
     - `weft/core/spawn_requests.py`
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-2.2]
   - Implement:
     - `WeftClient.from_context(...)`
     - `submit_taskspec(...)`
     - `submit_spec_reference(...)`
     - `submit_pipeline_reference(...)`
     - `status(...)`, `wait(...)`, `result(...)`, `stop(...)`, `kill(...)`,
       `events(...)`
     - stable lightweight result objects matching the 13C surface
   - Reuse:
     - existing context resolution and spawn submission helpers
     - existing result wait logic
     - existing spec-resolution helpers
   - Tests to add/update:
     - broker-backed submission and wait for inline command or function TaskSpecs
     - named spec resolution through the new client
     - pipeline reference submission through the new client
     - stop / kill delegation through existing control paths
   - Stop and re-evaluate if:
     - the implementation starts duplicating CLI normalization logic instead of
       wrapping the existing path
     - a second task-state representation appears outside Weft queues/logs
   - Done signal:
     - `weft.client` can submit and wait on real work through the manager path
       without importing `weft.commands.run` from callers.

2. Scaffold the in-repo `weft-django` package as a separately installable PyPI package.
   - Outcome: the repo contains `integrations/weft_django/` with standalone
     package metadata, local editable wiring, and no hard dependency from core
     Weft onto Django.
   - Files to touch:
     - `integrations/weft_django/pyproject.toml` (new)
     - `integrations/weft_django/README.md` (new)
     - `integrations/weft_django/weft_django/__init__.py` (new)
     - `pyproject.toml`
     - `README.md`
   - Read first:
     - `extensions/weft_docker/pyproject.toml`
     - `extensions/weft_macos_sandbox/pyproject.toml`
     - root `pyproject.toml`
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-0.2]
   - Implement:
     - standalone `project.name = "weft-django"`
     - dependency on `weft` and `django`
     - optional extras for `dev` and `channels`
     - root `pyproject.toml` convenience extra `django = ["weft-django>=...,<1"]`
       plus local editable source wiring under `[tool.uv.sources]`
   - Keep package metadata parallel to the existing extension packages where it
     helps, but do not force `weft_django` into the runner-plugin entry-point
     shape.
   - Tests to add/update:
     - lightweight package metadata checks, if needed, under
       `tests/system/` or integration-package tests
   - Stop and re-evaluate if:
     - the root package starts importing Django
     - the plan drifts toward making `weft_django` live under `extensions/`
   - Done signal:
     - `uv build` works in `integrations/weft_django/`
     - local repo development can install `weft-django` editably from the root.

3. Implement the decorator-backed Django function task lane.
   - Outcome: Django-owned synchronous background functions can be declared with
     `@weft_task`, registered via `weft_tasks.py`, and executed through the
     standard Weft function-task path.
   - Files to touch:
     - `integrations/weft_django/weft_django/apps.py`
     - `integrations/weft_django/weft_django/conf.py`
     - `integrations/weft_django/weft_django/registry.py`
     - `integrations/weft_django/weft_django/decorators.py`
     - `integrations/weft_django/weft_django/worker.py`
     - broker-backed tests in `integrations/weft_django/tests/`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-3.1], [DJ-3.2],
       [DJ-5.1], [DJ-6.1], [DJ-6.2], [DJ-7.1]
     - `weft/core/targets.py`
     - `weft/core/imports.py`
   - Implement:
     - `WeftDjangoConfig`
     - autodiscovery of `weft_tasks`
     - task registry with duplicate-name rejection
     - `@weft_task(...)`
     - wrapper target `weft_django.worker:run_registered_task`
     - structured work envelope with `task_name`, optional duplicate `tid`,
       `call.args`, `call.kwargs`, `request_id`
     - single-database wrapper behavior with documented multi-DB escape hatch
   - Preserve:
     - host-runner default only for decorated Django callables
     - no default `DJANGO_SETTINGS_MODULE` serialization into TaskSpec env
   - Tests to add/update:
     - real Django project fixture with `weft_tasks.py`
     - autodiscovery success and duplicate-name failure
     - decorated task execution through a real broker-backed Weft run
     - wrapper logging and request-id propagation if configured
     - rejection of `async def` tasks
   - Stop and re-evaluate if:
     - the wrapper starts growing domain-specific persistence rules
     - the code wants to bypass `function_target` execution or invent a
       Django-specific worker runtime
   - Done signal:
     - a decorated Django function executes via Weft and returns a normal Weft
       result through the outbox/result path.

4. Implement first-class native Weft submission helpers from Django.
   - Outcome: Django code can submit native TaskSpecs, spec refs, bundle-backed
     specs, and pipelines, with matching `..._on_commit()` helpers, without ad
     hoc glue.
   - Files to touch:
     - `integrations/weft_django/weft_django/client.py`
     - `integrations/weft_django/weft_django/__init__.py`
     - `integrations/weft_django/tests/...`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-8.1],
       [DJ-8.2], [DJ-8.4], [DJ-9.1]
     - new `weft/client.py`
   - Implement:
     - decorated task helpers:
       `enqueue(...)`, `enqueue_on_commit(...)`,
       `as_taskspec_for_call(*args, _overrides=None, **kwargs)`
     - generic native helpers:
       `submit_taskspec(...)`, `submit_taskspec_on_commit(...)`,
       `submit_spec_reference(...)`, `submit_spec_reference_on_commit(...)`,
       `submit_pipeline_reference(...)`,
       `submit_pipeline_reference_on_commit(...)`
     - local validation that deferred-submit helpers reject `wait=True`
   - Preserve:
     - native TaskSpecs and pipelines keep declared runner and target semantics
     - transaction hooks are thin wrappers around `transaction.on_commit()`
   - Tests to add/update:
     - `enqueue_on_commit()` with a real commit
     - rollback does not submit work
     - `wait=True` rejection on all deferred helper variants
     - native agent/bundle/pipeline submission through the Django helper layer
   - Stop and re-evaluate if:
     - helper code starts re-parsing spec refs instead of delegating to
       `weft.client`
     - decorator helpers and native helpers start diverging in result-handle
       semantics
   - Done signal:
     - Django code can submit either a decorated function task or a native
       Weft TaskSpec/pipeline through one stable integration package.

5. Add the read-only Django diagnostics surface.
   - Outcome: projects can opt into `weft_django.urls` for read-only task
     inspection and SSE streaming with explicit authorization hooks.
   - Files to touch:
     - `integrations/weft_django/weft_django/urls.py`
     - `integrations/weft_django/weft_django/views.py`
     - `integrations/weft_django/weft_django/sse.py`
     - `integrations/weft_django/weft_django/channels.py`
     - `integrations/weft_django/tests/...`
     - `integrations/weft_django/README.md`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-11.1],
       [DJ-11.2], [DJ-12.1], [DJ-12.2], [DJ-12.3]
   - Implement:
     - `GET /weft/tasks/<tid>/`
     - `GET /weft/tasks/<tid>/events/`
     - `AUTHZ` callable import and `ImproperlyConfigured` failure at URL import
     - SSE event stream using the new `weft.client` event iterator
     - optional Channels/WebSocket support behind the package's `channels` extra
   - Preserve:
     - these are diagnostics only, not domain-truth endpoints
     - no HTTP stop/kill
   - Tests to add/update:
     - URL import fails cleanly without `AUTHZ`
     - authorized read and stream requests succeed
     - unauthorized requests are rejected
     - SSE stream emits the expected event shape
     - Channels path only loads when the extra is present or is otherwise
       import-guarded cleanly
   - Stop and re-evaluate if:
     - the views start depending on Django models for task truth
     - control semantics creep into HTTP v1
   - Done signal:
     - projects can include the URLs and inspect live task state through
       read-only views backed by real Weft events.

6. Add Django management commands, testing support, and package docs.
   - Outcome: the package is operable and testable in the shapes promised by
     13C, with a README that explains migration from Celery-like habits and the
     real broker-vs-inline testing split.
   - Files to touch:
     - `integrations/weft_django/weft_django/management/commands/*.py`
     - `integrations/weft_django/README.md`
     - `README.md`
     - `docs/specifications/13C-Using_Weft_With_Django.md`
     - `docs/specifications/08-Testing_Strategy.md`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-15.1],
       [DJ-17.1], [DJ-17.2], [DJ-17.3]
     - `docs/specifications/08-Testing_Strategy.md`
   - Implement:
     - `manage.py weft_status`
     - `manage.py weft_task_status <tid>`
     - `manage.py weft_task_stop <tid>`
     - `manage.py weft_task_kill <tid>`
     - package README sections for:
       - install
       - `@weft_task`
       - native Weft submission helpers
       - migration table from Celery names to the explicit Django helpers
       - inline vs broker-backed tests
       - SSE/Channels operator notes
   - Tests to add/update:
     - command smoke tests in the integration package test tree
     - doc updates should add plan backlinks where appropriate
   - Stop and re-evaluate if:
     - management commands start diverging from the `weft.client` API
     - docs start promising a domain control-plane that the package does not
       implement
   - Done signal:
     - the package docs and commands make the supported lanes legible without
       implying a larger Django architecture than 13C actually promises.

7. Wire `weft-django` into shared CI and package-specific release automation.
   - Outcome: the repo tests the integration package on normal CI, the release
     helper understands the new first-party package, and a pushed
     `weft_django/vX.Y.Z` tag runs package-specific tests and publishes to PyPI
     through the shared release workflow.
   - Files to touch:
     - `.github/workflows/test.yml`
     - `.github/workflows/release-gate-django.yml` (new)
     - `.github/workflows/release.yml` (only if an input or assumption needs
       widening; otherwise reuse unchanged)
     - `bin/release.py`
     - `tests/system/test_release_script.py`
     - `README.md`
     - root `pyproject.toml` if CI install extras need widening
   - Read first:
     - `.github/workflows/release-gate-docker.yml`
     - `.github/workflows/release-gate-macos-sandbox.yml`
     - `.github/workflows/release.yml`
     - `.github/workflows/test.yml`
     - `bin/release.py`
     - `tests/system/test_release_script.py`
     - `docs/plans/2026-04-06-release-gated-tag-workflow-plan.md`
   - Implement:
     - add a new `ReleaseTarget` for `weft-django` in `bin/release.py`
     - add package metadata paths, tag namespace `weft_django`, test command,
       and helper output updates
     - update release-helper tests for the third first-party package
     - add `release-gate-django.yml` mirroring the existing extension gates,
       with:
       - trigger on `weft_django/v*`
       - package-local tests
       - call to `.github/workflows/release.yml` with
         `package_name=weft-django` and
         `package_dir=integrations/weft_django`
     - extend `test.yml` with a dedicated `test-django-integration` job and
       include the new package in lint / format / mypy paths where appropriate
     - update release docs in `README.md`
   - Preserve:
     - `.github/workflows/release.yml` remains the only publish workflow
     - package-specific release gates are tag-driven and test-before-publish
   - Tests to add/update:
     - release-helper tests for target discovery, dry-run tag pushes, and
       package build verification
     - structural YAML checks, if needed, for the new release-gate workflow
   - Stop and re-evaluate if:
     - the new package wants a second publish workflow
     - the CI change starts coupling the root package test matrix to Django in
       every job instead of a dedicated integration job
   - Done signal:
     - the repo can cut a `weft_django/vX.Y.Z` tag and publish the package
       through the same trusted-publishing path used by the existing optional
       packages.

8. Run slice-based review and final verification.
   - Outcome: the plan's required review loops and verification bar are met
     before calling the integration ready.
   - Review checkpoints:
     - after task 1 (`weft.client`)
     - after tasks 2–4 (`weft_django` submission surfaces)
     - after task 7 (CI/release automation)
   - Prefer a different agent family reviewer if available. Use the review
     prompt from `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.
   - Final verification should include:
     - targeted core client tests
     - integration package test suite in both inline and broker-backed modes
     - release-helper tests
     - local `uv build` in `integrations/weft_django/`
     - YAML sanity checks for `release-gate-django.yml`
   - Stop and re-evaluate if any reviewer says they could not implement the
     plan confidently from the current docs or if the work starts implying a
     broader Django control-plane than 13C allows.

## Testing Plan

- Keep the core `weft.client` proofs broker-backed. Do not mock away manager
  submission, result waiting, or control handling.
- Create a dedicated Django integration test project under
  `integrations/weft_django/tests/fixtures/` or equivalent. It should exercise:
  - app autodiscovery
  - decorated tasks
  - native TaskSpec submission
  - `transaction.on_commit()` behavior
  - read-only URLs and SSE
- Inline mode is acceptable only for narrow unit tests of decorated function
  wrappers. Use broker-backed tests for:
  - native TaskSpecs and bundles
  - native agent tasks
  - pipelines
  - `on_commit()` correctness
  - SSE / event-follow behavior
- Use a real cross-process-visible Django test database for broker-backed tests.
  In-memory SQLite is out of scope for those cases.
- Keep release-helper tests local and structural; do not attempt live GitHub or
  live PyPI tests.

## Verification

Run the smallest meaningful sets first, then the package-local suite:

```bash
./.venv/bin/python -m pytest tests/system/test_release_script.py -q
./.venv/bin/python -m pytest tests/core -q -k client
./.venv/bin/python -m pytest integrations/weft_django/tests -q
./.venv/bin/python -m mypy weft integrations/weft_django/weft_django --config-file pyproject.toml
./.venv/bin/python -m ruff check weft integrations/weft_django tests
./.venv/bin/python -m ruff format --check weft integrations/weft_django tests
(cd integrations/weft_django && uv build)
python3 - <<'PY'
import yaml
from pathlib import Path
workflow = yaml.safe_load(Path('.github/workflows/release-gate-django.yml').read_text())
assert workflow['on']['push']['tags'] == ['weft_django/v*']
publish = workflow['jobs']['publish-release']['with']
assert publish['package_name'] == 'weft-django'
assert publish['package_dir'] == 'integrations/weft_django'
PY
```

Success looks like:

- `weft.client` can submit and observe real work without CLI imports,
- the `weft_django` package can build and pass its own test suite,
- broker-backed Django tests prove `on_commit()` and native-spec submission,
- the repo-wide release helper recognizes `weft-django`,
- and a package-specific release gate exists that publishes `weft-django`
  through the shared trusted-publishing workflow.

## Out of Scope

- A domain-specific Django control plane for long-lived user-facing agent
  sessions
- App-owned artifact schemas or persistence rules beyond the generic linkage
  guidance in 13C
- A scheduler / beat / retry subsystem
- A generic async Python task declaration model in Weft core
- HTTP control endpoints for task stop / kill

# Using Weft With Django

This document specifies the intended contract for a first-party Django
integration package tentatively named `weft-django`.

It does not change current core Weft behavior on its own. It defines how a
framework integration should sit above Weft's substrate, what public API it
should expose to Django apps, and which small stable core API additions Weft
should provide so the integration does not depend on internal command modules.

Canonical current behavior still lives in the current specs:

- [00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)
- [02-TaskSpec.md](02-TaskSpec.md)
- [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)
- [05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)
- [10-CLI_Interface.md](10-CLI_Interface.md)
- [13-Agent_Runtime.md](13-Agent_Runtime.md)
- [13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md)

If this integration ships and becomes a maintained first-party surface, the
relevant core guarantees should be migrated into the owning canonical specs.

## Purpose [DJ-0]

Django is an interaction surface, not a Weft runner.

This document defines the Django integration layer for submitting and
inspecting Weft work from Django.

The integration exists to make Weft feel natural inside Django projects and
reusable Django apps without turning Weft into a Django-specific orchestration
engine.

The intended shape is:

- Weft remains the durable execution substrate.
- Django owns task declaration, submission helpers, transaction hooks, request
  integration, and optional operator UI.
- Domain truth stays in the Django application's own models and stores.
- Task lifecycle truth stays in Weft queues and task logs.

## Scope And Non-Goals [DJ-0.1]

This integration should provide:

- a Django-native decorator surface for Django-owned synchronous background
  functions
- first-class submission helpers for native Weft TaskSpecs, stored spec refs,
  bundle-backed task specs, and pipelines
- a stable submission and result API for project code and reusable apps
- transaction-safe enqueue helpers
- Django bootstrap around task execution
- optional read-only HTTP and realtime views for task inspection
- optional management-command and admin integration

This integration should not provide:

- a new `django` runner plugin
- a Django-owned task state database
- the full Django-side architecture for app-owned runtime rows, workflow
  artifacts, public records, or other domain artifacts
- the public control-plane model for persistent operator-facing, case-facing,
  or other long-lived user-facing agent sessions
- a hidden manager lifecycle inside web worker startup paths
- a Celery-style scheduler or beat equivalent in v1
- implicit retries, ETA scheduling, or countdown scheduling in v1
- framework magic that bypasses Weft's normal
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> runner` spine

Persistent operator-facing sessions are explicitly out of scope here. They need
a later Django control-plane spec that defines public thread or case identity,
authorization, and durable mapping from agent activity to domain artifacts.

### Recommended Lane Mapping [DJ-0.1.1]

Use the smallest surface that matches the lane. These are examples, not an
exhaustive taxonomy:

| Lane | Recommended Django integration surface |
| --- | --- |
| deterministic Django-owned synchronous background function | `@weft_task` |
| bundle-backed or agent-backed non-decorator work | native Weft TaskSpec or bundle submitted via `submit_spec_reference(...)` or `submit_taskspec(...)` |
| native Weft work plus deterministic persistence | native Weft TaskSpec or bundle plus app-owned deterministic persistence keyed by `weft_tid` |
| persistent operator-facing session | future separate Django control-plane surface; out of scope for this document |

## Repository And Package Placement [DJ-0.2]

The integration should live in this repository as a first-party optional
package, but not inside the existing `extensions/` runner-plugin directory.

Decision:

- repository: this repo
- package directory:
  `integrations/weft_django/`
- import package: `weft_django`
- PyPI package: `weft-django`
- optional convenience extra on the main package: `weft[django]`

Rationale:

- the integration will track Weft core closely and should evolve in the same
  repo while the Python client API is still settling
- this repo already uses a multi-package layout for optional first-party
  packages
- Django is not a runner, so placing it under `extensions/` would blur the
  runner boundary and make the architecture less legible

Explicit reevaluation milestone:

- keep the package in this repo until the public `weft.client` API has remained
  source-compatible across two consecutive Weft minor releases
- after that milestone, the project may split `weft-django` into a sibling repo
  if Django release cadence or ownership diverges materially from core Weft

The spec-owned import and package names should remain `weft_django` and
`weft-django` even if the code later moves.

_Planned implementation mapping_:

```text
integrations/
└── weft_django/
    ├── pyproject.toml
    ├── README.md
    ├── weft_django/
    │   ├── __init__.py
    │   ├── apps.py
    │   ├── conf.py
    │   ├── registry.py
    │   ├── decorators.py
    │   ├── client.py
    │   ├── worker.py
    │   ├── models.py           # only if the integration later needs its own
    │   │                       # non-authoritative config model; not for task truth
    │   ├── urls.py
    │   ├── views.py
    │   ├── sse.py
    │   ├── channels.py         # optional, only when Channels support is enabled
    │   ├── admin.py
    │   └── management/
    │       └── commands/
    └── tests/
```

## Layering And Ownership [DJ-1]

The ownership split is strict.

### What Weft core owns [DJ-1.1]

Weft core continues to own:

- `TaskSpec` shape and validation
- manager lifecycle
- spawn submission
- task control and state transitions
- queue-visible observability
- runner selection and isolation
- result delivery through outbox and task-log semantics

### What `weft_django` owns [DJ-1.2]

The Django package owns:

- Django app bootstrap
- task registry and autodiscovery
- transaction hooks
- Django-friendly submission helpers
- optional admin, HTTP, SSE, and WebSocket surfaces
- mapping between Django task declarations and ordinary Weft TaskSpecs

### What application code owns [DJ-1.3]

The host Django project or reusable app owns:

- domain models and database writes
- deciding when to enqueue work
- app-owned runtime or domain rows keyed by `weft_tid` when the workflow needs
  durable run identity or artifact linkage
- application-specific permissions around task inspection or control
- whether browser users need realtime task updates
- durable storage for app-specific artifacts and other domain records

## Required Core API Additions [DJ-2]

The integration must not depend on internal command handlers like
`weft.commands.run`.

Weft core therefore needs a stable public Python client surface.

### Public core client [DJ-2.1]

Weft should expose a public package:

- `weft.client`

with these stable entry points:

- `WeftClient.from_context(...)`
- `WeftClient.submit(...)`
- `WeftClient.submit_spec(...)`
- `WeftClient.submit_pipeline(...)`
- `WeftClient.submit_command(...)`
- `WeftClient.task(tid)`
- `WeftClient.tasks.*`
- `WeftClient.queues.*`
- `WeftClient.managers.*`
- `WeftClient.specs.*`
- `WeftClient.system.*`

Submission methods should return a `Task` handle whose stable minimum surface is:

- `tid`
- `snapshot()`
- `result(timeout=None)`
- `events(...)`
- `follow()`
- `stop()`
- `kill()`

The client should internally reuse the same context resolution, spawn
submission, and result-wait paths that the CLI uses today. It should not invent
second-path semantics.

Reference-resolution rule:

- `submit_spec(...)` should use the same resolution grammar as
  `weft run --spec NAME|PATH`, including stored spec names, direct file paths,
  and bundle directories
- `submit_pipeline(...)` should use the same resolution grammar as
  `weft run --pipeline NAME|PATH`

### Public core data shapes [DJ-2.2]

Weft should expose stable client-facing data objects:

- `Task`
- `TaskSnapshot`
- `TaskResult`
- `TaskEvent`

Minimum fields:

- `Task`: `tid`
- `TaskSnapshot`: `tid`, `name`, `status`, `return_code`, `started_at`,
  `completed_at`, `error`, `runtime_handle`, `metadata`
- `TaskResult`: `tid`, `status`, `value`, `stdout`, `stderr`, `error`
- `TaskEvent`: `tid`, `event_type`, `timestamp`, `payload`

The Django package should depend on this client API only. It should not import
`submit_spawn_request()` or other low-level internals directly.

## Django App Contract [DJ-3]

The integration should ship a standard Django app:

- app config: `weft_django.apps.WeftDjangoConfig`
- recommended `INSTALLED_APPS` entry: `"weft_django"`

### `AppConfig.ready()` rules [DJ-3.1]

`ready()` may:

- initialize the registry
- autodiscover task modules
- validate obvious configuration errors

`ready()` must not:

- start or ensure a manager
- submit tasks
- perform blocking broker health checks
- open long-lived queue watchers

The integration must preserve Weft's rule that ordinary execution should fail
on the normal path rather than hide speculative preflight behind framework
startup.

### Autodiscovery [DJ-3.2]

Default autodiscovery target:

- module name: `weft_tasks`

Behavior:

- each installed app may provide `weft_tasks.py`
- importing that module registers decorated tasks with the global registry
- missing modules are ignored
- import errors inside an existing module are surfaced as startup errors

This mirrors the part of Celery worth stealing: reusable apps can declare tasks
without owning project bootstrap.

## Task Registry [DJ-4]

The registry is the integration's public contract for named Django tasks.

### Registry responsibilities [DJ-4.1]

The registry stores:

- task name
- Python callable reference
- owning Django app label
- declaration defaults that map onto ordinary TaskSpec fields

The registry does not store:

- task runtime truth
- browser session state
- result payload copies outside Weft

### Public registry API [DJ-4.2]

The integration should expose:

- `weft_django.registry.get_task(name)`
- `weft_django.registry.iter_tasks()`
- `weft_django.registry.is_registered(name)`

The preferred calling surfaces remain:

- the decorated task object itself
- the public module-level client helpers

Registry access is for discovery and introspection, not the normal submission
path.

## Task Declaration Surface [DJ-5]

The v1 declaration surface should be decorator-based and function-only.

### Decorator [DJ-5.1]

Canonical decorator:

- `@weft_task(...)`

Role rule:

- `@weft_task` is for Django-owned synchronous Python background functions
- it is not the main declaration surface for interpretation agents, supervisor
  runs, or later persistent operator-facing sessions
- those lanes should usually remain native Weft TaskSpecs or bundle-backed
  tasks submitted through the first-class native submission helpers

Example:

```python
from weft_django import weft_task


@weft_task(name="billing.send_invoice", timeout=60, memory_mb=256)
def send_invoice(invoice_id: int, *, force: bool = False) -> dict[str, object]:
    from billing.service import send_invoice as run_send_invoice

    result = run_send_invoice(invoice_id=invoice_id, force=force)
    return {"invoice_id": invoice_id, "message_id": result.message_id}
```

### Supported decorator options [DJ-5.2]

The decorator should accept these task defaults:

- `name`
- `description`
- `timeout`
- `memory_mb`
- `cpu_percent`
- `stream_output`
- `runner`
- `runner_options`
- `working_dir`
- `env`
- `metadata`

These are declaration-time defaults. Per-call submission overrides remain
allowed for a small subset such as `timeout`, `metadata`, and `wait`.

Celery-compatibility rule:

- v1 does not expose `shared_task`
- v1 does not expose `delay`

Those names are false friends. Their Celery semantics are close enough to
encourage mechanical porting, but different enough to create delayed failures.
The package README should therefore include an explicit migration table that
maps common Celery call sites onto `@weft_task`, `enqueue()`, and
`enqueue_on_commit()`.

### Naming rules [DJ-5.3]

If `name` is omitted, the default public name is:

- `{module_path}.{function_name}`

Reusable apps should set an explicit stable name such as
`billing.send_invoice`.

Registry duplicate names are startup errors.

### Supported callable types [DJ-5.4]

V1 supports:

- synchronous Python callables only

V1 rejects:

- `async def` callables
- generators
- class-based task objects

The reason is current Weft function execution semantics. Async Django task
support can be added later only after Weft grows an explicit async callable
contract.

## Decorated Django Task To Weft Task Mapping [DJ-6]

Each decorated Django task is submitted as an ordinary Weft `function` task.

### Wrapper target [DJ-6.1]

The generated TaskSpec should use:

- `spec.type = "function"`
- `spec.function_target = "weft_django.worker:run_registered_task"`
- `spec.runner.name = "host"` by default

V1 runner rule:

- decorated Django tasks default to and validate for the `host` runner
- Django-initiated native Weft TaskSpecs and pipelines keep their own declared
  runner choices; `weft_django` must not silently rewrite them

The package may later allow other runners, but only when project code import
paths, settings bootstrap, and Django DB connectivity are explicitly supported
for that runner.

### Generated TaskSpec shape [DJ-6.2]

The wrapper TaskSpec should materialize ordinary Weft fields only.

Representative shape:

```jsonc
{
  "name": "billing.send_invoice",
  "spec": {
    "type": "function",
    "function_target": "weft_django.worker:run_registered_task",
    "runner": {
      "name": "host",
      "options": {}
    },
    "timeout": 60.0,
    "limits": {
      "memory_mb": 256
    }
  }
}
```

Initial work payload shape:

```jsonc
{
  "payload": {
    "task_name": "billing.send_invoice",
    "tid": "1837025672140161024",
    "call": {
      "args": [123],
      "kwargs": {"force": false}
    },
    "headers": {},
    "submitted_by": null,
    "request_id": null
  }
}
```

This keeps Django task execution inside the existing function-task path rather
than creating a new runtime class.

Wrapper-configuration rule:

- integration-owned fields such as `task_name` must not be stored in
  `spec.keyword_args`
- integration-owned wrapper configuration belongs in the structured work
  envelope, leaving user kwargs untouched
- the duplicated `tid` field in the work envelope is for application-side log
  correlation only; the worker's authoritative task identity remains the Weft
  task TID already known to the consumer runtime

### Pipeline composition scope [DJ-6.3]

Decorated Django tasks are leaf task declarations.

V1 supports:

- `task.as_taskspec_for_call(*args, _overrides=None, **kwargs)` for explicit
  manual composition into ordinary Weft task or pipeline specs

V1 does not support:

- a Django-specific pipeline DSL
- decorator-based DAG wiring
- Celery canvas analogues such as chains, groups, chords, or signatures

The integration should stay small here. Weft's existing pipeline model remains
the composition surface.

## Worker Bootstrap [DJ-7]

`weft_django.worker:run_registered_task` owns Django setup around the real task
call.

### Worker bootstrap steps [DJ-7.1]

The wrapper should:

1. resolve the Django settings module from inherited process environment by
   default
2. call `django.setup()`
3. call `django.db.close_old_connections()` before task execution
4. parse the structured work envelope and resolve the registered task by name
5. execute the task callable with the submitted args and kwargs
6. close old DB connections again on exit

Default bootstrap rule:

- the integration does not serialize `DJANGO_SETTINGS_MODULE` into TaskSpec env
  by default
- an explicit per-task env override is allowed only when the caller asks for it
- if the worker process environment does not provide a usable settings module,
  the task fails on the normal execution path with a clear startup error

### Worker bootstrap non-goals [DJ-7.2]

The wrapper should not:

- start a manager
- silently wrap all tasks in `transaction.atomic()`
- materialize ORM objects from IDs automatically
- catch and convert application exceptions into success results

### Multi-database note [DJ-7.3]

V1 assumes a single-database Django setup for its integration-owned wrapper
behavior.

Projects that depend on read replicas, schema-per-tenant routing, or other
multi-database bootstrap logic must extend `run_registered_task` or provide a
project-local wrapper that performs the required routing or tenant setup before
the registered task callable runs.

### Logging And Correlation [DJ-7.4]

`run_registered_task` should emit structured log context using these keys when
available:

- `task_name`
- `tid`
- `submitter_request_id`

Recommended correlation rule:

- if `WEFT_DJANGO["REQUEST_ID_PROVIDER"]` is configured, call that provider at
  enqueue time and copy the returned request ID into the submission envelope as
  `request_id`
- the worker wrapper should then log it as `submitter_request_id`

Weft remains the source of truth for lifecycle logs. These wrapper-level fields
exist only to make application logs easier to correlate with Weft task IDs.

## Submission API [DJ-8]

The Django package should expose two public submission styles:

- task-object methods
- native Weft submission helpers

### Task-object methods [DJ-8.1]

Decorated task objects should expose:

- `enqueue(*args, **kwargs) -> WeftSubmission`
- `enqueue_on_commit(*args, **kwargs) -> WeftDeferredSubmission`
- `as_taskspec_for_call(*args, _overrides=None, **kwargs) -> TaskSpec`

Canonical name:

- `enqueue`

V1 intentionally does not expose a Celery-compatible `apply_async(...)` with
unsupported scheduling kwargs such as `eta` or `countdown`.

`as_taskspec_for_call(...)` rule:

- positional and keyword arguments before `_overrides` become the task call
  payload
- `_overrides` carries TaskSpec-level overrides such as `timeout`, `metadata`,
  or runner configuration

This split avoids collisions between user task parameters and TaskSpec override
names.

### Native Weft Submission Helpers [DJ-8.2]

The integration should expose first-class helpers for native Weft work launched
from Django code:

- `weft_django.submit_taskspec(taskspec, *, work_payload=None, **overrides)`
- `weft_django.submit_taskspec_on_commit(taskspec, *, work_payload=None, **overrides)`
- `weft_django.submit_spec_reference(reference, *, input=None, **overrides)`
- `weft_django.submit_spec_reference_on_commit(reference, *, input=None, **overrides)`
- `weft_django.submit_pipeline_reference(reference, *, input=None, **overrides)`
- `weft_django.submit_pipeline_reference_on_commit(reference, *, input=None, **overrides)`

Native-submission rule:

- these helpers preserve the declared runner and target semantics of the
  supplied TaskSpec or resolved spec reference
- they exist so Django code can submit native Weft agent tasks, bundle-backed
  tasks, and pipelines without ad hoc glue

### Generic Module-Level Helpers [DJ-8.3]

The integration should also expose:

- `weft_django.get_client()`
- `weft_django.enqueue(task, *args, **kwargs)`
- `weft_django.enqueue_on_commit(task, *args, **kwargs)`
- `weft_django.status(tid)`
- `weft_django.result(tid, timeout=None)`
- `weft_django.stop(tid)`
- `weft_django.kill(tid)`

`task` may be:

- a decorated task object
- a registered task name string

### Submission Overrides [DJ-8.4]

Per-call submission kwargs should support:

- `metadata`
- `timeout`
- `wait`
- `stream_output`

Unsupported v1 per-call features:

- retries
- ETA / countdown
- queue routing in the Celery sense
- serializer selection

Deferred-submit rule:

- `enqueue_on_commit(..., wait=True)` is invalid and must raise `ValueError`
  locally
- `submit_taskspec_on_commit(..., wait=True)` is invalid and must raise
  `ValueError` locally
- `submit_spec_reference_on_commit(..., wait=True)` is invalid and must raise
  `ValueError` locally
- `submit_pipeline_reference_on_commit(..., wait=True)` is invalid and must
  raise `ValueError` locally

There is no submitted task yet while the outer transaction is still open, so a
wait contract would be misleading.

## Transaction Hooks [DJ-9]

Transaction-aware enqueue is required.

### Canonical hook [DJ-9.1]

`enqueue_on_commit()` must delegate to Django's `transaction.on_commit()`.

Behavior:

- inside an active transaction, submission is deferred until commit
- outside a transaction, submission happens immediately
- rollback prevents enqueue

This is the default-safe ORM integration point and should be the strongly
recommended surface for tasks that depend on freshly written database state.

### Request-response guidance [DJ-9.2]

The integration should document and enforce this guidance:

- use `enqueue_on_commit()` for write-following background work
- do not block a request on `wait=True` except in explicit operator or admin
  flows
- pass IDs or durable references, not model instances or querysets

## Result And Status Surface [DJ-10]

The canonical result surface is server-side Python, not browser transport.

### `WeftSubmission` / `WeftResult` object [DJ-10.1]

Submissions should return a lightweight handle with:

- `tid`
- `name`
- `status()`
- `wait(timeout=None)`
- `result(timeout=None)`
- `stop()`
- `kill()`
- `events(follow=False)`

This is the Django analogue of Celery's `AsyncResult`, but backed by the new
stable Weft Python client rather than a second result backend.

### Result semantics [DJ-10.2]

The integration should reuse Weft core result semantics:

- strings pass through as strings
- JSON-serializable values are returned as structured values
- non-serializable values may degrade to string output per core behavior

Preferred persistence rule:

- tasks should return small structured values or durable references
- app-owned artifacts and other user-facing domain state must not live only in
  Weft result payloads or streamed output
- if the task produces large or durable artifacts, deterministic Django service
  code should persist them explicitly and return a durable reference or small
  structured summary

### Artifact Handoff [DJ-10.3]

For many Django applications, Weft task output is candidate output, not the
final application artifact.

Recommended handoff:

1. a Weft task produces small structured output or a durable reference
2. deterministic Django service code validates or normalizes that output
3. the host app persists its own domain artifacts and linkage rows keyed by
   `weft_tid`

This keeps Weft as execution and lifecycle substrate while Django remains the
owner of durable domain artifacts.

## HTTP Surface [DJ-11]

The Django package should ship optional read-only URLs.

These generic views are operator diagnostics, not the main application UI.
Product UIs should normally read app-owned domain rows and artifacts rather
than raw Weft task state.

URL inclusion is opt-in:

- the host project includes `path("weft/", include("weft_django.urls"))`

### Endpoints [DJ-11.1]

V1 endpoints:

- `GET /weft/tasks/<tid>/`
  - returns current task snapshot and available result metadata
- `GET /weft/tasks/<tid>/events/`
  - returns a realtime stream

The URLs are convenience views over the Python client. They are not a second
source of truth and should not have their own persistence.

Control-endpoint rule:

- HTTP stop/kill endpoints are out of scope for v1
- if added later, they must be opt-in behind an explicit setting and documented
  threat model

### Authorization [DJ-11.2]

The HTTP surface should use an explicit authz callable, not Django model
permissions.

Setting:

- `WEFT_DJANGO["AUTHZ"] = "myapp.weft_authz:authorize"`

Callable contract:

- input: `(request, tid, action)`
- output: `bool`

Action values used by the shipped read surface:

- `"view"`
- `"stream"`

Default rule:

- including `weft_django.urls` without an `AUTHZ` callable is a configuration
  error

Failure timing:

- that configuration error should raise `django.core.exceptions.ImproperlyConfigured`
  during `weft_django.urls` import, not at `AppConfig.ready()` time and not on
  first request

This avoids creating a stub Django model only to host permission strings and
keeps domain authorization where the host project already owns it.

## Realtime Delivery [DJ-12]

Realtime delivery is optional and transport-specific.

The generic SSE and WebSocket surfaces are operator diagnostics. They are not a
substitute for a domain-specific read model in the main product UI.

### Default transport: SSE [DJ-12.1]

For one-way status and output updates, the default browser transport should be
Server-Sent Events.

Decision:

- default realtime endpoint: `GET /weft/tasks/<tid>/events/`
- transport: `text/event-stream`

Rationale:

- most task status UI is one-way
- SSE is materially simpler than a mandatory WebSocket/Channels dependency
- it fits Weft's inspect-and-follow model well

Operator note:

- under WSGI, SSE holds an HTTP connection open for the lifetime of the stream
  and therefore consumes a request worker per connected client
- production deployments that expect many concurrent viewers should serve the
  SSE endpoint from ASGI or use the optional Channels WebSocket transport

Event types should include:

- `snapshot`
- `state`
- `stdout`
- `stderr`
- `result`
- `end`

### Optional transport: WebSocket [DJ-12.2]

WebSocket support should be optional and require Django Channels.

Enablement:

- `channels` installed
- `WEFT_DJANGO["REALTIME"]["TRANSPORT"] = "channels"`

Default consumer route:

- `ws/weft/tasks/<tid>/`

WebSocket exists for:

- projects that already standardize on Channels
- future interactive or bidirectional task lanes
- browser UIs that prefer a single socket for status and future task-side
  interaction

WebSocket is not required for the normal result path.

### Realtime payload contract [DJ-12.3]

SSE and WebSocket should use the same JSON event payload shape:

```jsonc
{
  "tid": "1837025672140161024",
  "event_type": "state",
  "timestamp": 1837025672140162048,
  "payload": {
    "status": "running"
  }
}
```

## Django Settings Integration [DJ-13]

The integration should read configuration from a single Django setting:

- `WEFT_DJANGO`

Recommended shape:

```python
WEFT_DJANGO = {
    "CONTEXT": BASE_DIR,
    "AUTODISCOVER_MODULE": "weft_tasks",
    "AUTHZ": "myapp.weft_authz:authorize",
    "REQUEST_ID_PROVIDER": "myapp.request_id:get_current_request_id",
    "DEFAULT_TASK": {
        "runner": "host",
        "timeout": None,
        "memory_mb": 1024,
        "cpu_percent": None,
        "stream_output": False,
        "metadata": {},
    },
    "REALTIME": {
        "TRANSPORT": "sse",  # "none" | "sse" | "channels"
    },
}
```

### Settings precedence [DJ-13.1]

Configuration precedence should be:

1. per-call submission override
2. decorator declaration
3. `WEFT_DJANGO`
4. Weft core project config and `WEFT_*` environment
5. Weft core defaults

### Context selection [DJ-13.2]

Rule:

- explicit `WEFT_DJANGO["CONTEXT"]` wins when present
- otherwise the integration should fall through to ordinary Weft core context
  resolution, including `WEFT_*` environment overrides
- if neither an explicit Django setting nor a Weft core override is present,
  the integration should default to `settings.BASE_DIR`

Reason:

- web workers and management commands should not rely on the current working
  directory as the primary context signal

### `DJANGO_SETTINGS_MODULE` propagation [DJ-13.3]

Default behavior:

- `run_registered_task` should rely on inherited process environment for
  `DJANGO_SETTINGS_MODULE`
- the integration should not serialize that value into TaskSpec env by default

Opt-in override:

- a submitter may set `spec.env["DJANGO_SETTINGS_MODULE"]` deliberately for a
  specific task, but that is an explicit escape hatch, not the default path

### Request ID provider [DJ-13.4]

Optional setting:

- `WEFT_DJANGO["REQUEST_ID_PROVIDER"] = "myapp.request_id:get_current_request_id"`

Callable contract:

- input: none
- output: `str | None`

Default behavior:

- when unset, the integration does not auto-populate `request_id`
- when set, the submitter may call the provider at enqueue time and copy the
  returned value into the submission envelope for worker-side log correlation

## Exposure To Other Django Apps [DJ-14]

Reusable Django apps should be able to depend on the integration without
depending on project-local bootstrap code.

### Public calling patterns [DJ-14.1]

Other apps may:

- import a decorated task object and call `.enqueue()`
- call `weft_django.enqueue("billing.send_invoice", ...)`

Other apps should not:

- import `weft.commands.run` or other command-layer internals directly
- call `submit_spawn_request()` directly
- construct queue names manually

### Domain linking [DJ-14.2]

If a Django app needs durable workflow identity or artifact linkage, it should
create its own runtime or domain rows keyed by `weft_tid`.

Recommended pattern:

- Django app-owned rows record domain run identity, artifact linkage, delivery
  linkage, public-record linkage, or other workflow state keyed by `weft_tid`
- Weft remains the source of truth for task lifecycle and task output
- Django remains the source of truth for domain runs, domain artifacts, and
  user-facing state

The integration should not add a mirrored Django `TaskResult` model as the
authoritative record.

## Management Commands And Admin [DJ-15]

The first-party package should ship lightweight operational affordances.

### Management commands [DJ-15.1]

V1 should provide:

- `manage.py weft_status`
- `manage.py weft_task_status <tid>`
- `manage.py weft_task_stop <tid>`
- `manage.py weft_task_kill <tid>`

These are Django-native wrappers over the same client semantics used by the
Python API.

### Admin integration [DJ-15.2]

Admin integration is optional, but if shipped it must be read-only by default.

Preferred shape:

- a custom admin view that lists recent task snapshots via the Weft client
- drill-down view for one task's status, output, and events

The admin integration must not:

- persist duplicated task rows in Django models
- imply that Django admin is the source of truth for task lifecycle

## Serialization Rules [DJ-16]

The integration should be stricter in its documentation than core Weft is in
its fallback behavior.

Recommended rules for app authors:

- enqueue JSON-serializable args and kwargs only
- pass IDs, paths, or artifact references for large inputs
- do not enqueue ORM model instances, querysets, or open file handles
- return JSON-serializable values or plain strings

If the package performs explicit validation before submission, it should reject
obviously unserializable args and kwargs with a clear local error.

## Testing Strategy [DJ-17]

The integration should support both realistic broker-backed tests and an
explicit inline mode for narrow unit tests.

### Test modes [DJ-17.1]

Supported test modes:

- `broker`
  - normal Weft path; preferred for integration tests
- `inline`
  - execute the task callable in-process after transaction hooks fire; useful
    for unit tests

Inline mode must be opt-in through `WEFT_DJANGO["TEST_MODE"] = "inline"`.

The default remains real Weft submission so tests do not silently diverge from
real lifecycle behavior.

### Broker-mode test guidance [DJ-17.2]

Broker-mode tests must make process boundaries explicit.

Rules:

- the manager and worker processes must inherit the same Django test settings
  module as the launching test process
- broker context must be isolated per test process or xdist worker
- broker-mode tests require a database that is visible across processes;
  in-memory SQLite is therefore out of scope for broker-mode worker tests
- tasks that depend on committed ORM state should use `enqueue_on_commit()` and
  a test shape that actually commits, such as `TransactionTestCase` or the
  pytest-django equivalent

This applies to both `manage.py test` and `pytest-django`. Inline mode is the
cheap unit-test path. Broker mode is the correctness path.

### Lane-Specific Test Expectations [DJ-17.3]

Use test modes by lane:

- decorated synchronous function tasks: inline mode is acceptable for narrow
  unit tests
- native TaskSpecs, bundle-backed tasks, and native agent tasks: use broker-
  backed integration tests
- streaming behavior and follow-style result inspection: use broker-backed
  integration tests
- `on_commit` correctness: use broker-backed tests with real commits and real
  cross-process visibility

## Rejected Alternatives [DJ-18]

### Sibling repo now [DJ-18.1]

Rejected for v1.

Why:

- the integration needs a new stable core Python client
- keeping both in one repo reduces drift while the boundary settles

Future split trigger:

- reevaluate repo split only after the public `weft.client` surface has stayed
  source-compatible for two consecutive Weft minor releases

### `django` as a runner [DJ-18.2]

Rejected.

Why:

- Django is application bootstrap and interaction policy, not an execution
  backend
- Weft already separates task semantics from runner choice

### Django task table as result backend [DJ-18.3]

Rejected.

Why:

- it would create split truth against Weft queues and task logs
- it would pull Weft upward into a second control plane

### Mandatory WebSocket/Channels [DJ-18.4]

Rejected.

Why:

- one-way task updates do not need the complexity
- SSE is the simpler default
- Channels should remain optional

### HTTP control endpoints in v1 [DJ-18.5]

Rejected.

Why:

- a permission-check regression on stop/kill is a high-severity failure mode
- CLI and management-command control already cover operator needs
- control over HTTP should ship later, opt-in, with an explicit threat model

## Compatibility And Rollout [DJ-19]

Versioning rules:

- `weft-django` major version must track the supported Weft core major version
- while the stable core client is young, minor releases may carry paired
  compatibility requirements

Suggested install surfaces:

- `uv add weft-django`
- `uv add 'weft[django]'`

Monorepo rule:

- while `weft-django` lives in this repo, version coordination is a release
  management concern rather than a runtime mismatch concern

Once the package is split into a sibling repo:

- `weft-django` should fail clearly when the installed Weft core does not
  provide the required public client API

## Backlinks

- Active v1 reality-alignment plan:
  [../plans/2026-04-20-weft-django-v1-reality-alignment-plan.md](../plans/2026-04-20-weft-django-v1-reality-alignment-plan.md)
- Related implementation plan:
  [../plans/2026-04-20-weft-django-integration-implementation-plan.md](../plans/2026-04-20-weft-django-integration-implementation-plan.md)
- Core higher-level integration guidance:
  [13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md)
- Current agent runtime contract:
  [13-Agent_Runtime.md](13-Agent_Runtime.md)
- Current `TaskSpec` contract:
  [02-TaskSpec.md](02-TaskSpec.md)
- Current SimpleBroker/context contract:
  [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)
- Current CLI contract:
  [10-CLI_Interface.md](10-CLI_Interface.md)

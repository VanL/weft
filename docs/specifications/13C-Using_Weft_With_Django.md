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
- [13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md)

If this integration ships and becomes a maintained first-party surface, the
relevant core guarantees should be migrated into the owning canonical specs.

## Purpose [DJ-0]

Django is an interaction surface, not a Weft runner.

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

- a Django-native task declaration API
- a stable submission and result API for project code and reusable apps
- transaction-safe enqueue helpers
- Django bootstrap around task execution
- optional read-only HTTP and realtime views for task inspection
- optional management-command and admin integration

This integration should not provide:

- a new `django` runner plugin
- a Django-owned task state database
- a hidden manager lifecycle inside web worker startup paths
- a Celery-style scheduler or beat equivalent in v1
- implicit retries, ETA scheduling, or countdown scheduling in v1
- framework magic that bypasses Weft's normal
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> runner` spine

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

A later repo split is allowed if release cadence or ownership diverges, but the
spec-owned import and package names should remain `weft_django` and
`weft-django`.

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
- how to link task TIDs back into domain records
- application-specific permissions around task inspection or control
- whether browser users need realtime task updates

## Required Core API Additions [DJ-2]

The integration must not depend on internal command handlers like
`weft.commands.run`.

Weft core therefore needs a small stable Python client surface.

### Public core client [DJ-2.1]

Weft should expose a new public module:

- `weft.client`

with these stable entry points:

- `WeftClient.from_context(...)`
- `WeftClient.submit_taskspec(...)`
- `WeftClient.status(tid)`
- `WeftClient.wait(tid, timeout=None)`
- `WeftClient.result(tid, timeout=None)`
- `WeftClient.stop(tid)`
- `WeftClient.kill(tid)`
- `WeftClient.events(tid, *, follow=False, include_output=True)`

The client should internally reuse the same context resolution, spawn
submission, and result-wait paths that the CLI uses today. It should not invent
second-path semantics.

### Public core data shapes [DJ-2.2]

Weft should expose stable result objects:

- `SubmittedTask`
- `TaskSnapshot`
- `TaskResult`
- `TaskEvent`

Minimum fields:

- `SubmittedTask`: `tid`, `name`, `submitted_at`
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

Compatibility alias:

- `shared_task = weft_task`

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

## Django Task To Weft Task Mapping [DJ-6]

Each decorated Django task is submitted as an ordinary Weft `function` task.

### Wrapper target [DJ-6.1]

The generated TaskSpec should use:

- `spec.type = "function"`
- `spec.function_target = "weft_django.worker:run_registered_task"`
- `spec.runner.name = "host"` by default

V1 runner rule:

- decorated Django tasks default to and validate for the `host` runner

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
    "keyword_args": {
      "task_name": "billing.send_invoice"
    },
    "runner": {
      "name": "host",
      "options": {}
    },
    "timeout": 60.0,
    "limits": {
      "memory_mb": 256
    },
    "env": {
      "DJANGO_SETTINGS_MODULE": "config.settings"
    }
  }
}
```

Initial work payload shape:

```jsonc
{
  "payload": {
    "args": [123],
    "kwargs": {"force": false},
    "headers": {},
    "submitted_by": null
  }
}
```

This keeps Django task execution inside the existing function-task path rather
than creating a new runtime class.

## Worker Bootstrap [DJ-7]

`weft_django.worker:run_registered_task` owns Django setup around the real task
call.

### Worker bootstrap steps [DJ-7.1]

The wrapper should:

1. read `DJANGO_SETTINGS_MODULE` from the TaskSpec env or integration config
2. call `django.setup()`
3. call `django.db.close_old_connections()` before task execution
4. resolve the registered task by name
5. execute the task callable with the submitted args and kwargs
6. close old DB connections again on exit

### Worker bootstrap non-goals [DJ-7.2]

The wrapper should not:

- start a manager
- silently wrap all tasks in `transaction.atomic()`
- materialize ORM objects from IDs automatically
- catch and convert application exceptions into success results

## Submission API [DJ-8]

The Django package should expose two public submission styles:

- task-object methods
- module-level client helpers

### Task-object methods [DJ-8.1]

Decorated task objects should expose:

- `enqueue(*args, **kwargs) -> WeftSubmission`
- `enqueue_on_commit(*args, **kwargs) -> WeftDeferredSubmission`
- `delay(*args, **kwargs) -> WeftSubmission` as an alias for `enqueue`
- `as_taskspec(**overrides) -> TaskSpec`

Canonical name:

- `enqueue`

Celery migration alias:

- `delay`

V1 intentionally does not expose a Celery-compatible `apply_async(...)` with
unsupported scheduling kwargs such as `eta` or `countdown`.

### Module-level helpers [DJ-8.2]

The integration should also expose:

- `weft_django.get_client()`
- `weft_django.enqueue(task, *args, **kwargs)`
- `weft_django.enqueue_after_commit(task, *args, **kwargs)`
- `weft_django.status(tid)`
- `weft_django.result(tid, timeout=None)`
- `weft_django.stop(tid)`
- `weft_django.kill(tid)`

`task` may be:

- a decorated task object
- a registered task name string

### Submission overrides [DJ-8.3]

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

Application guidance:

- return strings, numbers, lists, dicts, or `None`
- if the task produces large or durable artifacts, store them explicitly and
  return a reference

## HTTP Surface [DJ-11]

The Django package should ship optional read-only URLs.

URL inclusion is opt-in:

- the host project includes `path("weft/", include("weft_django.urls"))`

### Endpoints [DJ-11.1]

V1 endpoints:

- `GET /weft/tasks/<tid>/`
  - returns current task snapshot and available result metadata
- `GET /weft/tasks/<tid>/events/`
  - returns a realtime stream
- `POST /weft/tasks/<tid>/stop/`
  - sends a STOP control request
- `POST /weft/tasks/<tid>/kill/`
  - sends a KILL control request

The URLs are convenience views over the Python client. They are not a second
source of truth and should not have their own persistence.

### Permissions [DJ-11.2]

Default authz:

- read views require authenticated users with `weft_django.view_task`
- control views require authenticated users with `weft_django.control_task`

The integration may expose mixins or hooks for custom project authz, but the
default must be conservative.

## Realtime Delivery [DJ-12]

Realtime delivery is optional and transport-specific.

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
- browser UIs that want one socket for status plus control

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
    "AUTOSTART": None,
    "DJANGO_SETTINGS_MODULE": "config.settings",
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

Default rule:

- if `WEFT_DJANGO["CONTEXT"]` is unset, default to `settings.BASE_DIR`

Reason:

- `build_context()` can auto-discover from the process working directory, but
  that is too implicit for web workers and management commands

### `DJANGO_SETTINGS_MODULE` propagation [DJ-13.3]

The worker wrapper should set `DJANGO_SETTINGS_MODULE` explicitly in task env
unless the submitter already overrode it.

## Exposure To Other Django Apps [DJ-14]

Reusable Django apps should be able to depend on the integration without
depending on project-local bootstrap code.

### Public calling patterns [DJ-14.1]

Other apps may:

- import a decorated task object and call `.enqueue()`
- call `weft_django.enqueue("billing.send_invoice", ...)`

Other apps should not:

- import `weft.commands.run`
- call `submit_spawn_request()` directly
- construct queue names manually

### Domain linking [DJ-14.2]

If a Django app needs to associate work with its own models, it should store
the Weft TID on its own model rows.

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

## Rejected Alternatives [DJ-18]

### Sibling repo now [DJ-18.1]

Rejected for v1.

Why:

- the integration needs a new stable core Python client
- keeping both in one repo reduces drift while the boundary settles

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

## Compatibility And Rollout [DJ-19]

Versioning rules:

- `weft-django` major version must track the supported Weft core major version
- while the stable core client is young, minor releases may carry paired
  compatibility requirements

Suggested install surfaces:

- `uv add weft-django`
- `uv add 'weft[django]'`

The integration should fail clearly when the installed Weft core does not
provide the required public client API.

## Backlinks

- Core higher-level integration guidance:
  [13B-Using_Weft_In_Higher_Level_Systems.md](13B-Using_Weft_In_Higher_Level_Systems.md)
- Current `TaskSpec` contract:
  [02-TaskSpec.md](02-TaskSpec.md)
- Current SimpleBroker/context contract:
  [04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)
- Current CLI contract:
  [10-CLI_Interface.md](10-CLI_Interface.md)

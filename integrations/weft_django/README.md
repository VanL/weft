# weft-django

`weft-django` is the first-party Django integration for Weft.

The package is typed (`py.typed`) and depends on Weft through the public
`weft.client` API.

It provides:

- `@weft_task` for Django-owned synchronous background functions
- transaction-safe submission helpers such as `enqueue_on_commit()`
- native TaskSpec, stored spec, and pipeline submission helpers
- read-only Django URLs for task inspection
- SSE by default, with optional Channels/WebSocket transport
- Django management commands for task status and control

Install:

```bash
uv add weft-django
```

Or from the main package convenience extra:

```bash
uv add "weft[django]"
```

Install the optional Channels transport with:

```bash
uv add "weft-django[channels]"
```

Equivalent install surfaces:

```bash
uv add "weft-django[realtime]"
uv add "weft[django-channels]"
```

Basic usage:

```python
from weft_django import weft_task


@weft_task(name="billing.send_invoice", timeout=60)
def send_invoice(invoice_id: int) -> dict[str, int]:
    return {"invoice_id": invoice_id}


submission = send_invoice.enqueue(123)
result = submission.result(timeout=30)
assert result.status == "completed"
```

## Submission Handle

`enqueue(...)` and the native submission helpers return `WeftSubmission`.

The handle exposes:

- `tid`
- `name`
- `status()`
- `wait(timeout=None)`
- `result(timeout=None)`
- `stop()`
- `kill()`
- `events(follow=False)`

`status()` returns the current public status string or `None` if no snapshot is
available yet. `wait()` and `result()` both return the structured Weft
`TaskResult`.

`enqueue_on_commit(...)` and the native `*_on_commit(...)` helpers return
`WeftDeferredSubmission`. The deferred handle has a stable `name` immediately
and gains `tid` plus task methods after the outer transaction commits. Calling
result-like methods before commit raises a local `RuntimeError`.

Deferred helpers validate and snapshot before registering Django's
`transaction.on_commit()` callback. Missing spec references, invalid overrides,
and unserializable payloads fail before the app transaction commits. Mutating
args, kwargs, or payload objects after helper call time does not change the work
submitted at commit.

## Native Helpers

Use these helpers when Django code wants to launch native Weft work instead of a
decorated Django function:

```python
from pathlib import Path

from weft_django import (
    submit_pipeline_reference,
    submit_spec_reference,
    submit_taskspec,
)


task = submit_taskspec(taskspec, payload={"job": 1})
task = submit_spec_reference(Path(".weft/tasks/report.json"), payload={"job": 1})
task = submit_pipeline_reference("nightly-report", payload={"job": 1})
```

Keyword rules:

- native helpers use `payload=...`
- `work_payload=...` and `input=...` are intentionally not supported
- deferred helpers reject `wait=True`

## Testing

`weft-django` does not ship an eager or inline execution mode.

Use the direct Python callable for narrow unit tests:

```python
assert send_invoice(123)["invoice_id"] == 123
```

Use broker-backed tests for enqueue behavior, transaction hooks, process
boundaries, streaming, native TaskSpecs, bundles, agents, and pipelines.

## Realtime And URLs

Include the read-only URLs explicitly:

```python
from django.urls import include, path

urlpatterns = [
    path("weft/", include("weft_django.urls")),
]
```

You must configure an authz callable:

```python
WEFT_DJANGO = {
    "AUTHZ": "myapp.weft_authz:authorize",
}
```

Supported realtime settings:

```python
WEFT_DJANGO = {
    "REALTIME": {
        "TRANSPORT": "sse",  # "none" | "sse" | "channels"
    },
}
```

Notes:

- `GET /weft/tasks/<tid>/` returns the current task snapshot
- `GET /weft/tasks/<tid>/events/` is the SSE endpoint when `TRANSPORT="sse"`
- `TRANSPORT="none"` disables the SSE endpoint
- `TRANSPORT="channels"` switches browser realtime delivery to the optional
  WebSocket consumer in `weft_django.channels`
- the Channels consumer starts a cancellable background stream after socket
  accept rather than blocking the connect lifecycle
- the HTTP and realtime surfaces are diagnostics only; they do not create a
  second task-truth store

## Celery Migration Guide

| Celery habit | `weft-django` |
| --- | --- |
| `@shared_task` | `@weft_task` |
| `task.delay(...)` | `task.enqueue(...)` |
| `transaction.on_commit(lambda: task.delay(...))` | `task.enqueue_on_commit(...)` |
| `AsyncResult` | `WeftSubmission` |

`delay` and `shared_task` are intentionally not shipped. They are close enough
to invite mechanical porting and far enough from Weft semantics to create
delayed failures.

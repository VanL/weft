# weft-django

`weft-django` is the first-party Django integration for Weft.

It provides:

- `@weft_task` for Django-owned synchronous background functions
- transaction-safe submission helpers such as `enqueue_on_commit()`
- native TaskSpec, stored spec, and pipeline submission helpers
- read-only Django URLs for task inspection
- Django management commands for task status and control

Install:

```bash
uv add weft-django
```

Or from the main package convenience extra:

```bash
uv add "weft[django]"
```

Basic usage:

```python
from weft_django import weft_task


@weft_task(name="billing.send_invoice", timeout=60)
def send_invoice(invoice_id: int) -> None:
    ...


submission = send_invoice.enqueue(123)
submission.result(timeout=30)
```

Celery migration guide:

| Celery habit | `weft-django` |
| --- | --- |
| `@shared_task` | `@weft_task` |
| `task.delay(...)` | `task.enqueue(...)` |
| `transaction.on_commit(lambda: task.delay(...))` | `task.enqueue_on_commit(...)` |

`delay` and `shared_task` are intentionally not shipped. They are close enough
to invite mechanical porting and far enough from Weft semantics to create
delayed failures.

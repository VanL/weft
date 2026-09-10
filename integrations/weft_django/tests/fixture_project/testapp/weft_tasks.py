from __future__ import annotations

from django.conf import settings

from testapp.models import EventRecord
from weft_django import weft_task
from weft_django.worker import get_current_request_id


@weft_task(name="testapp.echo_task", timeout=30.0)
def echo_task(value: str) -> str:
    return value


@weft_task(name="testapp.fetch_record_value", timeout=30.0)
def fetch_record_value(record_id: int) -> str:
    record = EventRecord.objects.get(pk=record_id)
    return str(record.value)


@weft_task(name="testapp.echo_current_request_id", timeout=30.0)
def echo_current_request_id() -> str | None:
    return get_current_request_id()


@weft_task(
    name="testapp.declared_task",
    description="declared",
    timeout=30.0,
    memory_mb=256,
    cpu_percent=50,
    stream_output=True,
    runner_options={"declared": True},
    working_dir=str(settings.BASE_DIR),
    env={"DECLARED": "1"},
    metadata={"declared_key": "declared", "tags": ["declared"]},
)
def declared_task(value: str) -> str:
    return value

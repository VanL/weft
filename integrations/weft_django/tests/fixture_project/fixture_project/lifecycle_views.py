"""Request-lifecycle fixture views for Weft client ownership tests."""

from __future__ import annotations

import json
import threading
from typing import Any

from django.db import transaction
from django.http import JsonResponse, StreamingHttpResponse

from weft_django import submit_taskspec, submit_taskspec_on_commit

REQUEST_THREADS: list[threading.Thread] = []
CONCURRENT_BARRIER: threading.Barrier | None = None


def _spec(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
        },
    }


def sync_burst(_request: Any) -> JsonResponse:
    REQUEST_THREADS.append(threading.current_thread())
    first = submit_taskspec(_spec("django-sync-first"), payload="first")
    second = submit_taskspec(_spec("django-sync-second"), payload="second")
    return JsonResponse({"tids": [first.tid, second.tid]})


def sync_on_commit_burst(_request: Any) -> JsonResponse:
    REQUEST_THREADS.append(threading.current_thread())
    with transaction.atomic():
        first = submit_taskspec_on_commit(_spec("django-commit-first"), payload="first")
        second = submit_taskspec_on_commit(
            _spec("django-commit-second"), payload="second"
        )
        assert first.tid is None
        assert second.tid is None
    return JsonResponse({"tids": [first.tid, second.tid]})


def concurrent_on_commit_burst(_request: Any) -> JsonResponse:
    REQUEST_THREADS.append(threading.current_thread())
    barrier = CONCURRENT_BARRIER
    if barrier is not None:
        barrier.wait(timeout=10)
    with transaction.atomic():
        deferred = submit_taskspec_on_commit(
            _spec("django-concurrent-commit"), payload="concurrent"
        )
    return JsonResponse({"tids": [deferred.tid]})


async def async_burst(_request: Any) -> JsonResponse:
    REQUEST_THREADS.append(threading.current_thread())
    first = submit_taskspec(_spec("django-async-first"), payload="first")
    second = submit_taskspec(_spec("django-async-second"), payload="second")
    return JsonResponse({"tids": [first.tid, second.tid]})


def streaming_burst(_request: Any) -> StreamingHttpResponse:
    REQUEST_THREADS.append(threading.current_thread())
    first = submit_taskspec(_spec("django-stream-first"), payload="first")

    def body() -> Any:
        REQUEST_THREADS.append(threading.current_thread())
        second = submit_taskspec(_spec("django-stream-second"), payload="second")
        yield json.dumps({"tids": [first.tid, second.tid]}).encode()

    return StreamingHttpResponse(body(), content_type="application/json")


def exception_burst(_request: Any) -> JsonResponse:
    REQUEST_THREADS.append(threading.current_thread())
    submit_taskspec(_spec("django-exception"), payload="accepted")
    raise RuntimeError("fixture response failure")

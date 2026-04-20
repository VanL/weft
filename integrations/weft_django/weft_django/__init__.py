"""Public Django-facing Weft integration surface."""

from __future__ import annotations

from weft_django.client import (
    DjangoWeftClient,
    WeftDeferredSubmission,
    WeftSubmission,
    enqueue,
    enqueue_on_commit,
    get_client,
    kill,
    result,
    status,
    stop,
    submit_pipeline_reference,
    submit_pipeline_reference_on_commit,
    submit_spec_reference,
    submit_spec_reference_on_commit,
    submit_taskspec,
    submit_taskspec_on_commit,
)
from weft_django.decorators import RegisteredWeftTask, weft_task

__all__ = [
    "DjangoWeftClient",
    "RegisteredWeftTask",
    "WeftDeferredSubmission",
    "WeftSubmission",
    "enqueue",
    "enqueue_on_commit",
    "get_client",
    "kill",
    "result",
    "status",
    "stop",
    "submit_pipeline_reference",
    "submit_pipeline_reference_on_commit",
    "submit_spec_reference",
    "submit_spec_reference_on_commit",
    "submit_taskspec",
    "submit_taskspec_on_commit",
    "weft_task",
]

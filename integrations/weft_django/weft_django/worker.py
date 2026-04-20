"""Django bootstrap wrapper for registered Weft tasks."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)
_CURRENT_ENVELOPE: ContextVar[dict[str, Any] | None] = ContextVar(
    "weft_django_current_envelope",
    default=None,
)


def _normalize_envelope(work_item: Any) -> dict[str, Any]:
    if isinstance(work_item, dict):
        payload = work_item.get("payload")
        if isinstance(payload, dict) and "task_name" in payload:
            return payload
        if "task_name" in work_item:
            return work_item
    raise ValueError("Registered Django task work item must contain a payload envelope")


def get_current_envelope() -> dict[str, Any] | None:
    """Return the currently executing registered-task envelope, if any."""

    return _CURRENT_ENVELOPE.get()


def get_current_request_id() -> str | None:
    """Return the currently executing submitter request id, if any."""

    envelope = get_current_envelope()
    if not isinstance(envelope, dict):
        return None
    request_id = envelope.get("request_id")
    return request_id if isinstance(request_id, str) else None


def run_registered_task(work_item: Any = None) -> Any:
    """Bootstrap Django, resolve the registered task, and execute it."""

    import django
    from django.db import close_old_connections

    from weft_django.registry import get_task

    django.setup()
    close_old_connections()
    token = None
    try:
        envelope = _normalize_envelope(work_item)
        token = _CURRENT_ENVELOPE.set(envelope)
        task_name = envelope.get("task_name")
        if not isinstance(task_name, str) or not task_name:
            raise ValueError("Registered task envelope is missing task_name")
        task = get_task(task_name)
        call = envelope.get("call")
        call = call if isinstance(call, Mapping) else {}
        args = call.get("args")
        kwargs = call.get("kwargs")
        call_args = list(args) if isinstance(args, list) else []
        call_kwargs = dict(kwargs) if isinstance(kwargs, dict) else {}
        logger.info(
            "Executing registered Django task",
            extra={
                "task_name": task_name,
                "tid": envelope.get("tid"),
                "submitter_request_id": envelope.get("request_id"),
            },
        )
        return task.func(*call_args, **call_kwargs)
    finally:
        if token is not None:
            _CURRENT_ENVELOPE.reset(token)
        close_old_connections()

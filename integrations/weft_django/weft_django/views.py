"""Read-only Django views over the Weft Python client."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from django.http import Http404, HttpRequest, HttpResponseForbidden, JsonResponse

from weft_django.client import get_core_client
from weft_django.conf import get_authz_callable
from weft_django.sse import sse_response


def _authorize(request: HttpRequest, tid: str, action: str) -> bool:
    authz = get_authz_callable(required=True)
    assert authz is not None
    return bool(authz(request, tid, action))


def task_detail_view(request: HttpRequest, tid: str) -> JsonResponse:
    if not _authorize(request, tid, "view"):
        return HttpResponseForbidden()
    snapshot = get_core_client().task(tid).snapshot()
    if snapshot is None:
        raise Http404(f"Unknown task: {tid}")
    payload: dict[str, Any] = asdict(snapshot)
    payload["result"] = {"status": snapshot.status}
    return JsonResponse(payload)


def task_events_view(request: HttpRequest, tid: str) -> Any:
    if not _authorize(request, tid, "stream"):
        return HttpResponseForbidden()
    return sse_response(tid)

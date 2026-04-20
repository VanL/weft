"""Optional read-only task inspection URLs."""

from __future__ import annotations

from django.urls import path

from weft_django.conf import get_authz_callable
from weft_django.views import task_detail_view, task_events_view

get_authz_callable(required=True)

urlpatterns = [
    path("tasks/<str:tid>/", task_detail_view, name="weft-task-detail"),
    path("tasks/<str:tid>/events/", task_events_view, name="weft-task-events"),
]

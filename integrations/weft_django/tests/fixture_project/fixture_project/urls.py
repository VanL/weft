from __future__ import annotations

from django.urls import include, path

from fixture_project import lifecycle_views

urlpatterns = [
    path("lifecycle/sync-burst/", lifecycle_views.sync_burst),
    path("lifecycle/sync-on-commit/", lifecycle_views.sync_on_commit_burst),
    path(
        "lifecycle/concurrent-on-commit/",
        lifecycle_views.concurrent_on_commit_burst,
    ),
    path("lifecycle/async-burst/", lifecycle_views.async_burst),
    path("lifecycle/streaming-burst/", lifecycle_views.streaming_burst),
    path("lifecycle/exception-burst/", lifecycle_views.exception_burst),
    path("weft/", include("weft_django.urls")),
]

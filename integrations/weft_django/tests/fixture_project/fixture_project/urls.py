from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("weft/", include("weft_django.urls")),
]

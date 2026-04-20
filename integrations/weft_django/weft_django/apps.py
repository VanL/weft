"""Django app config for the Weft integration."""

from __future__ import annotations

from django.apps import AppConfig

from weft_django.registry import autodiscover_tasks


class WeftDjangoConfig(AppConfig):
    name = "weft_django"
    verbose_name = "Weft Django"

    def ready(self) -> None:
        autodiscover_tasks()

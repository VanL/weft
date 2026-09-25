"""Django app config for the Weft integration."""

from __future__ import annotations

from django.apps import AppConfig

from weft_django.lifecycle import register_lifecycle_signals
from weft_django.registry import autodiscover_tasks


class WeftDjangoConfig(AppConfig):
    name = "weft_django"
    verbose_name = "Weft Django"

    def ready(self) -> None:
        register_lifecycle_signals()
        autodiscover_tasks()

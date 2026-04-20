"""Django management command for graceful Weft task stop."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from weft_django.client import stop


class Command(BaseCommand):
    help = "Gracefully stop a Weft task"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("tid")

    def handle(self, *args: Any, **options: Any) -> None:
        if not stop(options["tid"]):
            raise CommandError(f"Failed to stop task: {options['tid']}")
        self.stdout.write(options["tid"])

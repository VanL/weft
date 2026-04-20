"""Django management command for forceful Weft task kill."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from weft_django.client import kill


class Command(BaseCommand):
    help = "Force-kill a Weft task"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("tid")

    def handle(self, *args: Any, **options: Any) -> None:
        if not kill(options["tid"]):
            raise CommandError(f"Failed to kill task: {options['tid']}")
        self.stdout.write(options["tid"])

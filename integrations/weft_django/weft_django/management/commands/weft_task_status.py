"""Django management command for one Weft task status."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from weft_django.client import status


class Command(BaseCommand):
    help = "Show one Weft task snapshot"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("tid")

    def handle(self, *args: Any, **options: Any) -> None:
        snapshot = status(options["tid"])
        if snapshot is None:
            raise CommandError(f"Unknown task: {options['tid']}")
        self.stdout.write(json.dumps(asdict(snapshot), ensure_ascii=False))

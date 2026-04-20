"""Django management command for Weft task status overview."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from django.core.management.base import BaseCommand

from weft_django.client import get_core_client


class Command(BaseCommand):
    help = "Show recent Weft task snapshots"

    def handle(self, *args: Any, **options: Any) -> None:
        snapshots = get_core_client().tasks.list(include_terminal=True)
        self.stdout.write(
            json.dumps([asdict(snapshot) for snapshot in snapshots], ensure_ascii=False)
        )

"""Queue payload helpers shared by focused tests."""

from __future__ import annotations

import json
from typing import Any

from weft._constants import TERMINAL_ENVELOPE_TYPE


def terminal_envelopes(
    queue: Any,
    *,
    tid: str,
    source: str,
) -> list[dict[str, object]]:
    """Return typed terminal envelopes for one task from a queue peek."""

    messages = queue.peek_many(limit=50) or []
    envelopes: list[dict[str, object]] = []
    for raw in messages:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("type") == TERMINAL_ENVELOPE_TYPE
            and payload.get("tid") == tid
            and payload.get("source") == source
        ):
            envelopes.append(payload)
    return envelopes

"""Exact SimpleBroker message-ID boundary helpers.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.2]
"""

from __future__ import annotations

from typing import cast

from simplebroker import format_message_id


def normalize_exact_message_id(value: object) -> int:
    """Validate an exact message-ID input and normalize it to an integer.

    Integer inputs may use their natural Python representation. String inputs
    must already equal SimpleBroker's canonical 19-character ASCII form; this
    rejects padded and non-ASCII spellings that parse to the same integer.

    Spec: [SB-0.2]
    """

    canonical = format_message_id(cast("int | str", value))
    if isinstance(value, str) and value != canonical:
        raise ValueError(
            "message_id string must be exactly 19 ASCII decimal digits"
        )
    return int(canonical)


__all__ = ["normalize_exact_message_id"]

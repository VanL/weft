"""Shared full-TID spelling and exact SimpleBroker message-ID validation.

Spec references:
- docs/specifications/04-SimpleBroker_Integration.md [SB-0.2]
- docs/specifications/07-System_Invariants.md [OBS.5], [OBS.6]
"""

from __future__ import annotations

from typing import cast

from simplebroker import format_message_id
from weft._constants import TASKSPEC_TID_LENGTH


def is_task_tid(value: object) -> bool:
    """Return whether a value has full-TID spelling, without resolving it.

    Exact message-ID range validation remains with SimpleBroker below; TaskSpec
    additionally rejects zero. Spec: [OBS.5], [OBS.6], [SB-0.2].
    """
    return (
        isinstance(value, str)
        and len(value) == TASKSPEC_TID_LENGTH
        and value.isascii()
        and value.isdecimal()
    )


def normalize_exact_message_id(value: object) -> int:
    """Validate an exact message-ID input and normalize it to an integer.

    Integer inputs may use their natural Python representation. String inputs
    must already equal SimpleBroker's canonical 19-character ASCII form; this
    rejects padded and non-ASCII spellings that parse to the same integer.

    Spec: [SB-0.2]
    """

    canonical = format_message_id(cast("int | str", value))
    if isinstance(value, str) and not is_task_tid(value):
        raise ValueError("message_id string must be exactly 19 ASCII decimal digits")
    return int(canonical)


__all__ = ["is_task_tid", "normalize_exact_message_id"]

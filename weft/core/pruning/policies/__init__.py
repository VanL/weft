"""Reusable pruning policies.

Policy functions are pure candidate selectors. They do not open queues, delete
messages, write logs, or inspect TaskMonitor state.
"""

from __future__ import annotations

from .malformed import malformed_row_candidates
from .older_than import older_than_candidates

__all__ = [
    "malformed_row_candidates",
    "older_than_candidates",
]

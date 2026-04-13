"""Built-in agent runtime registration.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-7], [AR-9]
"""

from __future__ import annotations

from .backends import register_builtin_agent_runtimes

register_builtin_agent_runtimes()

__all__ = ["register_builtin_agent_runtimes"]

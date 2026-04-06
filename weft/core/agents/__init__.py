"""Built-in agent runtime registration."""

from __future__ import annotations

from .backends import register_builtin_agent_runtimes

register_builtin_agent_runtimes()

__all__ = ["register_builtin_agent_runtimes"]

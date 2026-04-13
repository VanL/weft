"""Built-in agent runtime backends.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-7], [AR-9]
"""

from __future__ import annotations

from weft.core.agent_runtime import register_agent_runtime

from .llm_backend import LLMBackend


def register_builtin_agent_runtimes() -> None:
    """Register built-in runtime adapters (Spec: [AR-7], [AR-9])."""
    try:
        register_agent_runtime("llm", LLMBackend())
    except ValueError as exc:
        if "already registered" not in str(exc):  # pragma: no cover - defensive
            raise


__all__ = ["LLMBackend", "register_builtin_agent_runtimes"]

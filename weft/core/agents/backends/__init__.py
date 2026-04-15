"""Built-in agent runtime backends.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-7], [AR-9]
"""

from __future__ import annotations

from ..runtime import register_agent_runtime
from .llm import LLMBackend
from .provider_cli import ProviderCLIBackend


def register_builtin_agent_runtimes() -> None:
    """Register built-in runtime adapters (Spec: [AR-7], [AR-9])."""
    try:
        register_agent_runtime("llm", LLMBackend())
    except ValueError as exc:
        if "already registered" not in str(exc):  # pragma: no cover - defensive
            raise
    try:
        register_agent_runtime("provider_cli", ProviderCLIBackend())
    except ValueError as exc:
        if "already registered" not in str(exc):  # pragma: no cover - defensive
            raise


__all__ = ["LLMBackend", "ProviderCLIBackend", "register_builtin_agent_runtimes"]

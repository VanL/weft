"""Built-in agent runtime registration without import-time adapter loading.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-7], [AR-9]
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ..runtime import register_agent_runtime

if TYPE_CHECKING:
    from .llm import LLMBackend
    from .provider_cli import ProviderCLIBackend


def register_builtin_agent_runtimes() -> None:
    """Register built-in runtime adapters (Spec: [AR-7], [AR-9])."""

    # Runtime adapters are loaded only at the explicit registration boundary so
    # schema-only imports do not initialize model clients or provider tooling.
    from .llm import LLMBackend
    from .provider_cli import ProviderCLIBackend

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


def __getattr__(name: str) -> Any:
    """Load a backend class only when its compatibility export is requested."""

    if name == "LLMBackend":
        value = import_module("weft.core.agents.backends.llm").LLMBackend
    elif name == "ProviderCLIBackend":
        value = import_module(
            "weft.core.agents.backends.provider_cli"
        ).ProviderCLIBackend
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return eager and lazy public names."""

    return sorted({*globals(), *__all__})

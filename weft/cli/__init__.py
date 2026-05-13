"""CLI adapter package."""

from __future__ import annotations

import importlib
from typing import Any

import typer

from weft._constants import PROG_NAME, __version__


class _LazyCliObject:
    """Proxy a CLI app object without importing the full Typer app eagerly."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def _resolve(self) -> Any:
        module = importlib.import_module("weft.cli.app")
        globals()[self._name] = self
        return getattr(module, self._name)


app = _LazyCliObject("app")
version_callback = _LazyCliObject("version_callback")

__all__ = ["PROG_NAME", "__version__", "app", "typer", "version_callback"]

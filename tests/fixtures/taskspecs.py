"""Backward compatibility imports for TaskSpec fixtures."""

from __future__ import annotations

from tests.taskspec import fixtures as _fixtures

__all__ = tuple(name for name in dir(_fixtures) if not name.startswith("_"))

for name in __all__:
    globals()[name] = getattr(_fixtures, name)

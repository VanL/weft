"""Suppress macOS xdist titles without changing native titles under test.

Spec: docs/specifications/08-Testing_Strategy.md [TS-0].
"""

from __future__ import annotations

import ast
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest


def without_worker_titles(source: str) -> str:
    """Select xdist's own no-title fallback before its native library import."""
    candidates = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.ImportFrom) and child.module == "setproctitle"
            for child in node.body
        )
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "Unsupported xdist bootstrap: expected optional title import"
        )
    block = candidates[0]
    expected = ast.parse(
        "try:\n"
        "    from setproctitle import setproctitle\n"
        "except ImportError:\n"
        "    def setproctitle(title: str) -> None:\n"
        "        pass\n"
    ).body[0]
    # Compare structure, not formatting. Defaults/decorators can execute code
    # even when the function body is pass, so accept only the known fallback.
    if ast.dump(block) != ast.dump(expected):
        raise RuntimeError("Unsupported xdist bootstrap: expected no-title fallback")
    fallback = block.handlers[0].body[0]
    lines = source.splitlines(keepends=True)
    lines[block.lineno - 1 : block.end_lineno] = [ast.unparse(fallback) + "\n"]
    return "".join(lines)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_getremotemodule() -> str | None:
    """Adapt only macOS worker bootstrap; production imports remain untouched."""
    if sys.platform != "darwin":
        return None
    module = find_spec("xdist.remote")
    if module is None or module.origin is None:
        raise RuntimeError("Cannot locate xdist worker bootstrap source")
    source = Path(module.origin).read_text(encoding="utf-8")
    return without_worker_titles(source)

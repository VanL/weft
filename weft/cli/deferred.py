"""Deferred CLI dependencies (Spec: 10-CLI_Interface.md [CLI-1.1.1]).

Each accessor here holds a function-level import under a named exception the
owner granted to the import rule in ``CLAUDE.md`` §4.2. Named exceptions:

- prompt-toolkit, ``get_prompt_session()``: the interactive prompt library
  costs 26–56 ms at import and is used only by interactive runs; the owner
  granted this one exception on 2026-11-03.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prompt_toolkit import PromptSession


def get_prompt_session() -> type[PromptSession[str]]:
    """Load the interactive prompt class only when an interactive run starts."""
    # CLAUDE.md §4.2 named exception (owner, 2026-11-03): interactive-only import.
    from prompt_toolkit import PromptSession

    return PromptSession

"""Prompt-template helpers for agent work envelopes.

This module keeps template rendering outside runtime adapters. Template support
in this slice is intentionally small: exact ``{{ name }}`` substitution with
strict validation of missing and extra variables.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-2.2], [AR-3]
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from weft.core.taskspec import AgentTemplateSection

_TEMPLATE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def render_agent_template(
    template: AgentTemplateSection,
    template_args: Mapping[str, object] | None,
) -> str:
    """Render ``template.prompt`` using exact ``{{ name }}`` substitution."""
    values = dict(template_args or {})
    placeholders = tuple(_TEMPLATE_PATTERN.findall(template.prompt))
    missing = sorted({name for name in placeholders if name not in values})
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing template argument(s): {joined}")

    extra = sorted(set(values) - set(placeholders))
    if extra:
        joined = ", ".join(extra)
        raise ValueError(f"Unexpected template argument(s): {joined}")

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = values[name]
        return str(value)

    return _TEMPLATE_PATTERN.sub(_replace, template.prompt)


__all__ = ["render_agent_template"]

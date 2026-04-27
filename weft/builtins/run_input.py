"""Built-in adapters for spec-declared run input.

Spec references:
- docs/specifications/02-TaskSpec.md [TS-1], [TS-1.4A]
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
"""

from __future__ import annotations

from collections.abc import Mapping

from weft.core.taskspec.run_input import SpecRunInputRequest


def arguments_payload(request: SpecRunInputRequest) -> dict[str, str]:
    """Return declared run-input arguments as the initial work payload."""

    return dict(request.arguments)


def nonempty_arguments_payload(request: SpecRunInputRequest) -> dict[str, str]:
    """Return declared run-input arguments, rejecting empty string values."""

    return _reject_empty_values(request.arguments)


def keyword_arguments_payload(
    request: SpecRunInputRequest,
) -> dict[str, dict[str, str]]:
    """Return declared run-input arguments as a function kwargs envelope."""

    return {"kwargs": dict(request.arguments)}


def nonempty_keyword_arguments_payload(
    request: SpecRunInputRequest,
) -> dict[str, dict[str, str]]:
    """Return declared run-input arguments as kwargs, rejecting empty strings."""

    return {"kwargs": _reject_empty_values(request.arguments)}


def _reject_empty_values(arguments: Mapping[str, str]) -> dict[str, str]:
    payload = dict(arguments)
    for name, value in payload.items():
        if not value.strip():
            raise ValueError(f"spec.run_input argument '{name}' must not be empty")
    return payload


__all__ = [
    "arguments_payload",
    "keyword_arguments_payload",
    "nonempty_arguments_payload",
    "nonempty_keyword_arguments_payload",
]

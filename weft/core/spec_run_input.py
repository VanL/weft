"""Helpers for spec-declared ``weft run --spec`` input shaping.

Spec references:
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1]
- docs/specifications/02-TaskSpec.md [TS-1]
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from weft.core.imports import import_callable_ref, split_import_ref

RUN_COMMAND_RESERVED_OPTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "spec",
        "pipeline",
        "input",
        "function",
        "arg",
        "kw",
        "env",
        "name",
        "interactive",
        "non-interactive",
        "stream-output",
        "no-stream-output",
        "timeout",
        "memory",
        "cpu",
        "tag",
        "context",
        "wait",
        "no-wait",
        "json",
        "verbose",
        "monitor",
        "continuous",
        "once",
        "autostart",
        "no-autostart",
        "help",
    }
)


@dataclass(frozen=True, slots=True)
class SpecRunInputRequest:
    """Submission-time request passed to a spec-owned input adapter."""

    arguments: dict[str, str]
    stdin_text: str | None
    context_root: str | None
    spec_name: str


@dataclass(frozen=True, slots=True)
class ParsedDeclaredOptions:
    """Parsed declared long options plus any untouched trailing tokens."""

    arguments: dict[str, str]
    remaining_tokens: list[str]


def normalize_declared_option_name(name: str) -> str:
    """Return the long-option form for a declared argument name."""
    return name.replace("_", "-")


def validate_run_input_adapter_ref(ref: str) -> str:
    """Validate and normalize an adapter import ref."""
    normalized = ref.strip()
    if not normalized:
        raise ValueError("spec.run_input.adapter_ref must be a non-empty string")
    split_import_ref(normalized)
    return normalized


def normalize_run_input_value(kind: str, raw_value: str) -> str:
    """Normalize one declared CLI value for adapter consumption."""
    if kind == "string":
        return raw_value
    if kind == "path":
        return str(Path(raw_value).expanduser().resolve())
    raise ValueError(f"Unsupported spec.run_input argument type: {kind}")


def parse_declared_option_args(
    tokens: list[str],
    arguments: Mapping[str, Any],
    *,
    allow_unknown: bool,
    apply_defaults: bool,
) -> ParsedDeclaredOptions:
    """Parse declared long options from raw trailing CLI tokens."""
    option_to_name = {
        normalize_declared_option_name(name): name for name in arguments.keys()
    }
    parsed: dict[str, str] = {}
    remaining_tokens: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--") or token == "--":
            raise ValueError(
                "Spec-declared run arguments only support long options in "
                "`--name value` or `--name=value` form"
            )

        option_text = token[2:]
        if not option_text:
            raise ValueError("Invalid empty declared option '--'")

        if "=" in option_text:
            option_name, raw_value = option_text.split("=", 1)
            consumed_tokens = [token]
            index += 1
        else:
            option_name = option_text
            if index + 1 >= len(tokens):
                raise ValueError(f"Declared option '--{option_name}' requires a value")
            next_token = tokens[index + 1]
            if next_token.startswith("--"):
                raise ValueError(f"Declared option '--{option_name}' requires a value")
            raw_value = next_token
            consumed_tokens = [token, next_token]
            index += 2

        declared_name = option_to_name.get(option_name)
        if declared_name is None:
            if allow_unknown:
                remaining_tokens.extend(consumed_tokens)
                continue
            raise ValueError(
                f"Option '--{option_name}' is not declared by this TaskSpec"
            )
        if declared_name in parsed:
            raise ValueError(f"Declared option '--{option_name}' may not be repeated")

        normalized = normalize_run_input_value(arguments[declared_name].type, raw_value)
        choices = tuple(getattr(arguments[declared_name], "choices", ()))
        if choices:
            normalized_choices = {
                normalize_run_input_value(arguments[declared_name].type, choice)
                for choice in choices
            }
            if normalized not in normalized_choices:
                raise ValueError(
                    f"Declared option '--{option_name}' must be one of: "
                    + ", ".join(sorted(choices))
                )
        parsed[declared_name] = normalized

    if apply_defaults:
        for name, declaration in arguments.items():
            default = getattr(declaration, "default", None)
            if name in parsed or default is None:
                continue
            normalized_default = normalize_run_input_value(declaration.type, default)
            choices = tuple(getattr(declaration, "choices", ()))
            if choices:
                normalized_choices = {
                    normalize_run_input_value(declaration.type, choice)
                    for choice in choices
                }
                if normalized_default not in normalized_choices:
                    raise ValueError(
                        f"Declared option '--{normalize_declared_option_name(name)}' "
                        "default must be one of: " + ", ".join(sorted(choices))
                    )
            parsed[name] = normalized_default

    missing = [
        f"--{normalize_declared_option_name(name)}"
        for name, declaration in arguments.items()
        if declaration.required and name not in parsed
    ]
    if missing:
        raise ValueError(
            "Missing required declared option(s): " + ", ".join(sorted(missing))
        )
    return ParsedDeclaredOptions(
        arguments=parsed,
        remaining_tokens=remaining_tokens,
    )


def parse_declared_run_input_args(
    tokens: list[str],
    arguments: Mapping[str, Any],
) -> dict[str, str]:
    """Parse raw trailing CLI tokens against a declared run-input contract."""
    parsed = parse_declared_option_args(
        tokens,
        arguments,
        allow_unknown=False,
        apply_defaults=False,
    )
    return parsed.arguments


def ensure_json_serializable_work_payload(payload: object) -> None:
    """Require a JSON-serializable payload before queueing a spawn request."""
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "spec.run_input adapter must return a JSON-serializable work payload"
        ) from exc


def validate_run_input_adapter(
    adapter_ref: str,
    *,
    bundle_root: str | Path | None = None,
) -> None:
    """Validate that a run-input adapter ref is importable and callable."""
    import_callable_ref(adapter_ref, bundle_root=bundle_root)


def invoke_run_input_adapter(
    adapter_ref: str,
    *,
    request: SpecRunInputRequest,
    bundle_root: str | Path | None = None,
) -> object:
    """Invoke a run-input adapter and require a JSON-serializable payload."""
    adapter = import_callable_ref(adapter_ref, bundle_root=bundle_root)
    try:
        payload = adapter(request)
    except TypeError as exc:
        raise TypeError(
            f"spec.run_input adapter {adapter_ref!r} could not be called with "
            "SpecRunInputRequest"
        ) from exc
    ensure_json_serializable_work_payload(payload)
    return payload


__all__ = [
    "ParsedDeclaredOptions",
    "RUN_COMMAND_RESERVED_OPTION_NAMES",
    "SpecRunInputRequest",
    "ensure_json_serializable_work_payload",
    "invoke_run_input_adapter",
    "normalize_declared_option_name",
    "parse_declared_option_args",
    "parse_declared_run_input_args",
    "validate_run_input_adapter",
    "validate_run_input_adapter_ref",
]

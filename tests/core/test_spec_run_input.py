"""Tests for spec-declared ``weft run --spec`` input shaping."""

from __future__ import annotations

from pathlib import Path

import pytest

from weft.core.taskspec import RunInputArgumentSection
from weft.core.taskspec.run_input import (
    SpecRunInputRequest,
    invoke_run_input_adapter,
    parse_declared_run_input_args,
)


def test_parse_declared_run_input_args_parses_string_and_path_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    arguments = {
        "prompt": RunInputArgumentSection(type="string", required=True),
        "document": RunInputArgumentSection(type="path", required=False),
    }

    parsed = parse_declared_run_input_args(
        ["--prompt", "Summarize this document", "--document", "doc.txt"],
        arguments,
    )

    assert parsed == {
        "prompt": "Summarize this document",
        "document": str((tmp_path / "doc.txt").resolve()),
    }


def test_parse_declared_run_input_args_rejects_missing_required_option() -> None:
    arguments = {
        "prompt": RunInputArgumentSection(type="string", required=True),
    }

    with pytest.raises(ValueError, match="Missing required declared option"):
        parse_declared_run_input_args([], arguments)


def test_invoke_run_input_adapter_supports_bundle_local_module(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def build(request):",
                "    return {'task': f\"{request.arguments['prompt']}::{request.stdin_text}\"}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = invoke_run_input_adapter(
        "helper_module:build",
        request=SpecRunInputRequest(
            arguments={"prompt": "Summarize"},
            stdin_text="hello",
            context_root=str(tmp_path),
            spec_name="bundle-spec",
        ),
        bundle_root=bundle_root,
    )

    assert payload == {"task": "Summarize::hello"}


def test_invoke_run_input_adapter_rejects_non_json_serializable_payload(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "helper_module.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "def build(request):",
                "    return object()",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="JSON-serializable work payload"):
        invoke_run_input_adapter(
            "helper_module:build",
            request=SpecRunInputRequest(
                arguments={},
                stdin_text=None,
                context_root=str(tmp_path),
                spec_name="bundle-spec",
            ),
            bundle_root=bundle_root,
        )
